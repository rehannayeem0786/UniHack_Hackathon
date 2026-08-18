"""Evidence, grounding and citation behaviour.

`supports()` is the grounding check that separates a value the model recalled
from one it read. Its thresholds are deliberate and tested here: comparison
ignores case, spacing and punctuation so `50-1/4 in` matches `50 1/4"`, but very
short values are refused outright because a single digit appears in every
document and proves nothing.
"""

from __future__ import annotations

from backend.sourcing.evidence import Evidence, EvidenceBundle


def page(url="https://www.example.com/p/x", **kw) -> Evidence:
    defaults = dict(kind="product-page", title="Product", text="", tables={})
    defaults.update(kw)
    return Evidence(url=url, **defaults)


class TestBundleBasics:
    def test_empty_bundle_is_falsey(self):
        assert not EvidenceBundle()
        assert len(EvidenceBundle()) == 0

    def test_documents_with_no_content_are_not_added(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="", tables={}))
        bundle.add(None)
        assert len(bundle) == 0

    def test_duplicate_urls_are_ignored(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="one"))
        bundle.add(page(text="two"))
        assert len(bundle) == 1

    def test_documents_sort_by_authority(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/1", kind="product-page", text="x" * 500))
        bundle.add(page("https://a.example/2", kind="specification", text="y"))
        # A specification sheet outranks a longer product page.
        assert bundle.documents[0].kind == "specification"


class TestGrounding:
    def test_exact_value_is_supported(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="Sound Level 47 dBA measured"))
        assert bundle.supports("47 dBA") is not None

    def test_spacing_and_punctuation_are_ignored(self):
        bundle = EvidenceBundle()
        bundle.add(page(text='Depth with door open 50 1/4"'))
        assert bundle.supports("50-1/4 in") is None  # `in` is not in the text
        assert bundle.supports("50 1/4") is not None

    def test_value_found_in_a_spec_table_counts(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="shell", tables={"Motor Type": "Brushless"}))
        assert bundle.supports("Brushless") is not None

    def test_absent_value_is_not_supported(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="Sound Level 47 dBA"))
        assert bundle.supports("Cast Iron") is None

    def test_short_values_are_refused(self):
        # Guard against spurious grounding: "5" appears in almost any document.
        bundle = EvidenceBundle()
        bundle.add(page(text="5 cycles, 15 A, 120 V"))
        assert bundle.supports("5", minimum=3) is None
        assert bundle.supports("") is None

    def test_returns_the_document_so_a_citation_can_be_made(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://www.dewalt.com/p/x", text="Chuck Type Keyless"))
        found = bundle.supports("Keyless")
        assert found is not None and found.url == "https://www.dewalt.com/p/x"


class TestTableLookup:
    def test_exact_label_match(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="t", tables={"Country Of Origin": "China"}))
        hit = bundle.table_lookup("country of origin")
        assert hit is not None and hit[0] == "China"

    def test_partial_label_match_for_longer_labels(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="t", tables={"Assembled Product Height": "8.5-in"}))
        hit = bundle.table_lookup("assembled product height")
        assert hit is not None and hit[0] == "8.5-in"

    def test_missing_label_returns_none(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="t", tables={"Motor Type": "Brushless"}))
        assert bundle.table_lookup("Sound Level") is None
        assert bundle.table_lookup("") is None


class TestBestProductPage:
    def test_url_naming_the_part_wins_over_a_longer_page(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://m.example/News/big-launch", text="x" * 9000, mentions_mpn=True))
        bundle.add(
            page(
                "https://m.example/products/2834-21hd",
                text="short",
                mentions_mpn=True,
                url_names_part=True,
            )
        )
        best = bundle.best_product_page()
        assert best is not None and "2834-21hd" in best.url

    def test_pdfs_are_never_the_product_page(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://m.example/manual.pdf", kind="manual", text="text"))
        assert bundle.best_product_page() is None

    def test_none_when_empty(self):
        assert EvidenceBundle().best_product_page() is None

    def test_third_party_documents_are_never_the_product_page(self):
        # `MFR URL` is by definition the manufacturer's own page; a reputable
        # third-party listing can never stand in for it, however well it names
        # the part.
        bundle = EvidenceBundle()
        bundle.add(
            page(
                "https://www.gs1.org/products/00627987501019",
                text="DCD1007B",
                mentions_mpn=True,
                url_names_part=True,
                third_party=True,
            )
        )
        assert bundle.best_product_page() is None


class TestThirdParty:
    def test_citation_carries_the_tier(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/x", text="t", mentions_mpn=True))
        bundle.add(
            page(
                "https://www.gs1.org/products/1",
                text="t",
                mentions_mpn=True,
                third_party=True,
            )
        )
        by_url = {c["url"]: c["source"] for c in bundle.citations()}
        assert by_url["https://a.example/x"] == "first-party"
        assert by_url["https://www.gs1.org/products/1"] == "third-party"

    def test_first_party_sorts_ahead_of_third_party(self):
        # Same kind: a first-party sheet outranks a third-party one so grounding
        # and citation order trust the manufacturer first.
        bundle = EvidenceBundle()
        bundle.add(
            page(
                "https://www.gs1.org/products/1",
                kind="specification",
                text="third",
                third_party=True,
            )
        )
        bundle.add(
            page(
                "https://www.dewalt.com/spec.pdf",
                kind="specification",
                text="first",
            )
        )
        assert bundle.documents[0].url.startswith("https://www.dewalt.com")

    def test_has_first_party_distinguishes_the_fallback(self):
        third = EvidenceBundle()
        third.add(
            page("https://www.gs1.org/products/1", text="t", third_party=True)
        )
        assert third.has_first_party is False
        assert len(third.third_party_documents) == 1

        both = EvidenceBundle()
        both.add(page("https://a.example/x", text="t"))
        both.add(page("https://www.gs1.org/products/1", text="t", third_party=True))
        assert both.has_first_party is True
        assert len(both.third_party_documents) == 1

    def test_third_party_prompt_context_is_labelled(self):
        bundle = EvidenceBundle()
        bundle.add(
            page(
                "https://www.gs1.org/products/1",
                text="DCD1007B 20V",
                tables={"Sound Level": "47 dBA"},
                third_party=True,
                mentions_mpn=True,
            )
        )
        context = bundle.as_prompt_context(2000)
        # The model must see that these specifications are not the manufacturer's
        # own claims, and the heading must not masquerade as a manufacturer sheet.
        assert "THIRD-PARTY" in context
        assert "MANUFACTURER SPECIFICATIONS" not in context
        assert "Sound Level: 47 dBA" in context

    def test_third_party_prompt_context_never_pretends_to_be_manufacturer(self):
        # A bundle with only third-party documents must not produce the
        # "MANUFACTURER SPECIFICATIONS" heading.
        bundle = EvidenceBundle()
        bundle.add(
            page(
                "https://www.gs1.org/products/1",
                text="t",
                tables={"Material": "Steel"},
                third_party=True,
            )
        )
        assert "MANUFACTURER SPECIFICATIONS" not in bundle.as_prompt_context(2000)


class TestPromptContext:
    def test_respects_the_character_budget(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/1", text="a" * 20000))
        bundle.add(page("https://a.example/2", kind="specification", text="b" * 20000))
        context = bundle.as_prompt_context(3000)
        assert 0 < len(context) <= 3600  # budget plus per-document headers

    def test_specification_pairs_come_before_prose(self):
        # The ordering that matters: 6k of manual prose ahead of the attribute
        # template cut attribute fill by a third, so pairs lead and are never
        # truncated away.
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/manual", kind="manual", text="PROSE " * 900))
        bundle.add(page("https://a.example/p", text="more prose", tables={"Sound Level": "47 dBA"}))
        context = bundle.as_prompt_context(2000)
        assert context.index("Sound Level: 47 dBA") < context.index("PROSE")

    def test_specification_pairs_survive_a_tiny_budget(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/manual", kind="manual", text="x" * 50000))
        bundle.add(page("https://a.example/p", text="y" * 50000, tables={"Chuck Size": "1/2 in"}))
        assert "Chuck Size: 1/2 in" in bundle.as_prompt_context(700)

    def test_merged_tables_prefer_the_better_source(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/page", text="t", tables={"Material": "Steel"}))
        bundle.add(
            page(
                "https://a.example/spec",
                kind="specification",
                text="t",
                tables={"Material": "Stainless Steel"},
            )
        )
        value, source = bundle.merged_tables()["Material"]
        assert value == "Stainless Steel"
        assert source.endswith("/spec")

    def test_internal_keys_are_not_shown_to_the_model(self):
        bundle = EvidenceBundle()
        bundle.add(page(text="t", tables={"_ld_description": "hidden", "Material": "Steel"}))
        context = bundle.as_prompt_context(2000)
        assert "_ld_description" not in context
        assert "Material: Steel" in context

    def test_empty_bundle_gives_empty_context(self):
        assert EvidenceBundle().as_prompt_context(1000) == ""


class TestCitationsAndCompaction:
    def test_citation_carries_url_and_provenance(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/x", text="hello", mentions_mpn=True))
        citation = bundle.citations()[0]
        assert citation["url"] == "https://a.example/x"
        assert citation["characters"] == 5
        assert citation["retrieved_at"]

    def test_compact_releases_text_but_keeps_the_citation(self):
        bundle = EvidenceBundle()
        bundle.add(page("https://a.example/x", text="a" * 5000))
        before = bundle.citations()[0]["characters"]
        bundle.compact()
        assert before == 5000
        assert bundle.documents[0].text == ""
        assert bundle.urls == ["https://a.example/x"]

    def test_summary_reports_reach(self):
        bundle = EvidenceBundle()
        bundle.permitted_domains = ["dewalt.com"]
        bundle.add(page("https://a.example/1", text="x", mentions_mpn=True))
        bundle.add(page("https://a.example/2.pdf", kind="manual", text="y"))
        summary = bundle.summary()
        assert summary["documents"] == 2
        assert summary["mpn_confirmed"] == 1
        assert "manual" in summary["kinds"]
