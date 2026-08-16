
"""The sourcing stage's manufacturer-URL resolution.

The URL hierarchy is the point: a verified product page from research wins,
then the domain learned from the labelled rows, then the seeded official-domain
registry — and an unknown brand gets nothing at all, because a fabricated URL
is worse than an empty cell.
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
from backend.pipeline.agents.sourcing import SourcingAgent


@pytest.fixture
def kb() -> KnowledgeBase:
    """A minimal knowledge base with no learned asset data."""
    return KnowledgeBase(
        taxonomy=TaxonomyRegistry(
            triple_to_paths={}, templates={}, leaf_to_path={}, product_names={}
        ),
        manufacturers=ManufacturerRegistry(supplier_pairs={}),
        attributes=AttributeVocabulary(values={}, uoms={}, label_values={}),
        assets=AssetRegistry(),
    )


def record(mpn="DC5004BG", brand="Speed Queen®", manufacturer="Alliance Laundry Systems LLC"):
    return ProductRecord(
        part_number="25285947",
        raw_mpn=mpn,
        mpn=mpn,
        brand_name=brand,
        manufacturer_name=manufacturer,
        raw_description=f"{mpn} gas dryer",
    )


class TestSourcingUrl:
    def test_learned_domain_wins(self, kb):
        kb.assets.brand_domain["speed queen"] = "https://www.speedqueen.com"
        rec = SourcingAgent(kb).run(record())
        assert rec.mfr_url == "https://www.speedqueen.com"

    def test_official_registry_is_the_fallback(self, kb):
        # Nothing learned from the training fold; the seeded registry still
        # knows Speed Queen's own site, so the row is not left unsourced.
        rec = SourcingAgent(kb).run(record())
        assert rec.mfr_url == "https://speedqueen.com"
        assert rec.provenance["mfr_url"] == "approved-manufacturer-domain (no deep link found)"
        assert rec.confidence["mfr_url"] == 0.7

    def test_manufacturer_name_also_resolves(self, kb):
        rec = SourcingAgent(kb).run(record(brand=""))
        assert rec.mfr_url == "https://alliancelaundry.com"

    def test_unknown_brand_gets_no_url(self, kb):
        rec = SourcingAgent(kb).run(record(brand="Totally Made Up Brand Ltd", manufacturer=""))
        assert rec.mfr_url is None
        assert "mfr_url" not in rec.provenance

    def test_verified_deep_link_is_never_overwritten(self, kb):
        rec = record()
        rec.mfr_url = "https://speedqueen.com/products/dc5004bg/"
        rec.provenance["mfr_url"] = "verified product page, part number confirmed on page"
        SourcingAgent(kb).run(rec)
        assert rec.mfr_url == "https://speedqueen.com/products/dc5004bg/"
        assert "verified" in rec.provenance["mfr_url"]
