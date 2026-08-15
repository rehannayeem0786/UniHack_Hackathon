"""The research stage, driven by a fake fetcher so no test touches the network.

These are the behaviours that make retrieval safe to ship, and each one is a
decision rather than an accident:

* a page that does not mention the part number is discarded, however plausible
* a press release on the right domain is not a source for one product's specs
* the `MFR URL` is the page whose URL names the part, not the longest page
* retrieved documents become `Ref URL 1-5`, in the order they were read
* a record with no reachable source degrades to a note, not an exception
"""

from __future__ import annotations

import pytest

from backend.core.schema import ProductRecord
from backend.knowledge.assets import AssetRegistry
from backend.knowledge.registry import (
    AttributeVocabulary,
    KnowledgeBase,
    ManufacturerRegistry,
    TaxonomyRegistry,
)
from backend.pipeline.agents.research import ResearchAgent
from backend.sourcing.fetch import Document


class FakeFetcher:
    """Serves canned documents by URL and records what was requested."""

    def __init__(self, pages: dict[str, Document]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def document(self, url: str, permitted: set[str]) -> Document | None:
        self.requested.append(url)
        return self.pages.get(url)


def html_doc(url, text="", tables=None, links=None, title="Product") -> Document:
    return Document(
        url=url,
        status=200,
        kind="html",
        title=title,
        text=text,
        tables=tables or {},
        links=links or [],
    )


def pdf_doc(url, text="manual text") -> Document:
    return Document(url=url, status=200, kind="pdf", title=url.rsplit("/", 1)[-1], text=text)


@pytest.fixture
def kb() -> KnowledgeBase:
    """A minimal knowledge base: the research stage only reads asset domains."""
    return KnowledgeBase(
        taxonomy=TaxonomyRegistry(
            triple_to_paths={}, templates={}, leaf_to_path={}, product_names={}
        ),
        manufacturers=ManufacturerRegistry(supplier_pairs={}),
        attributes=AttributeVocabulary(values={}, uoms={}, label_values={}),
        assets=AssetRegistry(),
    )


def record(mpn="DCD1007B", brand="DEWALT") -> ProductRecord:
    return ProductRecord(
        part_number="1",
        raw_mpn=mpn,
        mpn=mpn,
        brand_name=brand,
        manufacturer_name="Stanley Black & Decker",
        raw_description=f"{mpn} drill",
    )


def run(kb, pages, monkeypatch, rec=None, candidates=None):
    """Run the stage with discovery stubbed to a fixed candidate list."""
    from backend.pipeline.agents import research as module

    monkeypatch.setattr(
        module.discovery, "candidates", lambda *a, **k: candidates or list(pages)
    )
    agent = ResearchAgent(kb, fetcher=FakeFetcher(pages))
    rec = rec or record()
    return agent.run(rec), agent


class TestVerification:
    def test_page_mentioning_the_part_is_accepted(self, kb, monkeypatch):
        url = "https://www.dewalt.com/en-us/product/dcd1007b/hammer-drill"
        pages = {url: html_doc(url, text="DCD1007B 20V MAX hammer drill")}
        rec, _ = run(kb, pages, monkeypatch)
        assert len(rec.evidence) == 1
        assert rec.mfr_url == url
        assert rec.citations[0]["url"] == url

    def test_page_not_mentioning_the_part_is_discarded(self, kb, monkeypatch):
        url = "https://www.dewalt.com/en-us/product/other/thing"
        pages = {url: html_doc(url, text="A completely different tool")}
        rec, _ = run(kb, pages, monkeypatch)
        assert len(rec.evidence) == 0
        assert rec.mfr_url is None
        assert "did not mention" in rec.evidence.note

    def test_editorial_page_is_skipped_even_when_it_names_the_part(self, kb, monkeypatch):
        url = "https://www.dewalt.com/News/Press-Releases/launch"
        pages = {url: html_doc(url, text="DCD1007B and six other new tools")}
        rec, _ = run(kb, pages, monkeypatch)
        assert len(rec.evidence) == 0
        assert rec.mfr_url is None


class TestUrlSelection:
    def test_url_naming_the_part_becomes_the_mfr_url(self, kb, monkeypatch):
        listing = "https://www.dewalt.com/en-us/products/drills"
        product = "https://www.dewalt.com/en-us/product/dcd1007b/hammer-drill"
        pages = {
            listing: html_doc(listing, text="DCD1007B " + "filler " * 3000),
            product: html_doc(product, text="DCD1007B hammer drill"),
        }
        rec, _ = run(kb, pages, monkeypatch, candidates=[listing, product])
        assert rec.mfr_url == product

    def test_confidence_reflects_confirmation(self, kb, monkeypatch):
        url = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        rec, _ = run(kb, {url: html_doc(url, text="DCD1007B")}, monkeypatch)
        assert rec.confidence["mfr_url"] == 0.95
        assert "confirmed" in rec.provenance["mfr_url"]


class TestDocuments:
    def test_pdfs_become_reference_urls(self, kb, monkeypatch):
        product = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        spec = "https://assets.dewalt.com/spec-sheet.pdf"
        manual = "https://assets.dewalt.com/DCD1007B-installation.pdf"
        pages = {
            product: html_doc(
                product,
                text="DCD1007B",
                links=[(spec, "Specification Sheet"), (manual, "Installation Manual")],
            ),
            spec: pdf_doc(spec, "DCD1007B specification"),
            manual: pdf_doc(manual, "DCD1007B installation"),
        }
        rec, _ = run(kb, pages, monkeypatch, candidates=[product])
        refs = [rec.extras.get(f"Ref URL {i}") for i in range(1, 6)]
        assert spec in refs
        assert manual in refs
        assert rec.mfr_url == product

    def test_non_product_documents_are_not_fetched(self, kb, monkeypatch):
        product = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        brochure = "https://assets.dewalt.com/marketing-brochure.pdf"
        pages = {
            product: html_doc(product, text="DCD1007B", links=[(brochure, "Brochure")]),
            brochure: pdf_doc(brochure),
        }
        _, agent = run(kb, pages, monkeypatch, candidates=[product])
        assert brochure not in agent.fetcher.requested


class TestEvidenceColumns:
    def test_iso_country_codes_are_expanded(self, kb, monkeypatch):
        # DeWALT publishes `CN`; the delivery format writes the country name.
        url = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        pages = {url: html_doc(url, text="DCD1007B", tables={"Country Of Origin": "CN"})}
        rec, _ = run(kb, pages, monkeypatch)
        assert rec.extras["Country Of Origin"] == "China"

    def test_country_and_dimensions_are_copied_with_a_citation(self, kb, monkeypatch):
        url = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        pages = {
            url: html_doc(
                url,
                text="DCD1007B",
                tables={
                    "Country Of Origin": "China",
                    "Assembled Product Height": "8.5-in",
                    "Assembled Product Weight": "3.23-lbs",
                },
            )
        }
        rec, _ = run(kb, pages, monkeypatch)
        assert rec.extras["Country Of Origin"] == "China"
        assert rec.extras["HEIGHT"] == "8-1/2"  # decimal inches become fractions
        assert rec.extras["HEIGHT_UOM"] == "in"
        assert rec.extras["WEIGHT"] == "3.23"
        assert rec.extras["WEIGHT_UOM"] == "lb"
        # Every copied column carries the URL it came from.
        assert rec.grounded["Country Of Origin"] == url
        assert url in rec.provenance["HEIGHT"]

    def test_unrelated_labels_are_not_invented(self, kb, monkeypatch):
        url = "https://www.dewalt.com/en-us/product/dcd1007b/x"
        pages = {url: html_doc(url, text="DCD1007B", tables={"Motor Type": "Brushless"})}
        rec, _ = run(kb, pages, monkeypatch)
        assert "Country Of Origin" not in rec.extras
        assert "HEIGHT" not in rec.extras


class TestDegradation:
    def test_no_permitted_domain_records_a_note(self, kb, monkeypatch):
        unknown = ProductRecord(
            part_number="1",
            raw_mpn="XYZ123",
            mpn="XYZ123",
            brand_name="Totally Made Up Brand",
            manufacturer_name="Totally Made Up Holdings",
        )
        rec, _ = run(kb, {}, monkeypatch, rec=unknown)
        assert len(rec.evidence) == 0
        assert "no approved manufacturer domain" in rec.evidence.note
        assert rec.citations == []

    def test_missing_part_number_is_handled(self, kb, monkeypatch):
        blank = ProductRecord(part_number="1", raw_mpn="", mpn="", brand_name="DEWALT")
        rec, _ = run(kb, {}, monkeypatch, rec=blank)
        assert rec.evidence.note == "no part number to research"

    def test_no_candidates_records_a_note(self, kb, monkeypatch):
        rec, _ = run(kb, {}, monkeypatch, candidates=[])
        assert "no candidate URL" in rec.evidence.note

    def test_discovery_failure_does_not_raise(self, kb, monkeypatch):
        from backend.pipeline.agents import research as module

        def boom(*a, **k):
            raise RuntimeError("search is down")

        monkeypatch.setattr(module.discovery, "candidates", boom)
        agent = ResearchAgent(kb, fetcher=FakeFetcher({}))
        rec = agent.run(record())
        # A retrieval outage must degrade the row, never fail it.
        assert len(rec.evidence) == 0
        assert rec.mfr_url is None
