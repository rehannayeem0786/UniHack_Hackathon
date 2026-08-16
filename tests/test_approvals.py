"""Approvals (Standard/Approvals) extraction, grounded in retrieved evidence.

The reference output fills the certification column far more often than the
model echoes one back, so the attribute stage also reads certifications
straight off the retrieved first-party source. These tests pin that behaviour:

* a certification stated verbatim in the evidence is copied across
* a certification absent from the evidence is never invented
* the model's answer supplements the evidence rather than replacing it
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
from backend.pipeline.agents.attributes import AttributeAgent
from backend.sourcing.evidence import Evidence, EvidenceBundle


class StubLLM:
    """Returns a canned JSON result without touching the network."""

    def __init__(self, result: dict | None) -> None:
        self._result = result
        self.available = False

    def generate_json(self, prompt, schema, system=None):
        return self._result


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase(
        taxonomy=TaxonomyRegistry(
            triple_to_paths={},
            templates={"Appliances > Large Appliances > Dishwashers": ["Color"]},
            leaf_to_path={},
            product_names={},
        ),
        manufacturers=ManufacturerRegistry(supplier_pairs={}),
        attributes=AttributeVocabulary(values={}, uoms={}, label_values={}),
        assets=AssetRegistry(),
        approvals=["UL Listed", "ENERGY STAR Certified", "RoHS Compliant"],
    )


def record() -> ProductRecord:
    rec = ProductRecord(
        part_number="1",
        raw_mpn="PDSH4816AF",
        mpn="PDSH4816AF",
        brand_name="FRIGIDAIRE®",
        classpath="Appliances > Large Appliances > Dishwashers",
        raw_description="PDSH4816AF Dishwasher SS",
    )
    bundle = EvidenceBundle()
    bundle.add(
        Evidence(
            url="https://www.frigidaire.com/p/PDSH4816AF",
            kind="specification",
            title="PDSH4816AF spec sheet",
            text="PDSH4816AF dishwasher. Certifications: UL Listed, ENERGY STAR Certified.",
        )
    )
    rec.evidence = bundle
    return rec


class TestApprovalsFromEvidence:
    def test_verbatim_certifications_are_copied(self, kb):
        rec = record()
        AttributeAgent(kb, StubLLM({"attributes": []})).run(rec)
        assert "UL Listed" in rec.approvals
        assert "ENERGY STAR Certified" in rec.approvals
        assert rec.grounded["Standard/Approvals"] == "https://www.frigidaire.com/p/PDSH4816AF"

    def test_absent_certifications_are_not_invented(self, kb):
        rec = record()
        AttributeAgent(kb, StubLLM({"attributes": []})).run(rec)
        assert "RoHS Compliant" not in rec.approvals

    def test_model_answer_supplements_evidence(self, kb):
        rec = record()
        llm = StubLLM({"attributes": [], "approvals": ["RoHS Compliant"]})
        AttributeAgent(kb, llm).run(rec)
        assert rec.approvals == ["ENERGY STAR Certified", "RoHS Compliant", "UL Listed"]

    def test_no_evidence_leaves_the_field_to_the_model(self, kb):
        rec = ProductRecord(
            part_number="1",
            raw_mpn="X1",
            mpn="X1",
            classpath="Appliances > Large Appliances > Dishwashers",
            raw_description="X1 Dishwasher",
        )
        AttributeAgent(kb, StubLLM({"attributes": [], "approvals": ["UL Listed"]})).run(rec)
        assert rec.approvals == ["UL Listed"]


class TestBrandConventionFallback:
    def test_learned_brand_set_fills_an_otherwise_empty_field(self, kb):
        kb.brand_approvals = {"ACME®": ["RoHS Compliant", "Title 20 Exempt"]}
        rec = ProductRecord(
            part_number="1",
            raw_mpn="X1",
            mpn="X1",
            brand_name="ACME®",
            classpath="Appliances > Large Appliances > Dishwashers",
            raw_description="X1 Dishwasher",
        )
        AttributeAgent(kb, StubLLM({"attributes": []})).run(rec)
        assert rec.approvals == ["RoHS Compliant", "Title 20 Exempt"]
        assert "brand-level convention" in rec.provenance["approvals"]

    def test_brand_set_never_overrides_grounded_evidence(self, kb):
        kb.brand_approvals = {"FRIGIDAIRE®": ["RoHS Compliant"]}
        rec = record()  # evidence states UL Listed + ENERGY STAR Certified
        AttributeAgent(kb, StubLLM({"attributes": []})).run(rec)
        assert "RoHS Compliant" not in rec.approvals
        assert "UL Listed" in rec.approvals

    def test_fit_learns_only_a_dominant_nonempty_set(self):
        import pandas as pd

        from backend.knowledge.registry import KnowledgeBase

        def row(brand, approvals):
            base = {c: "" for c in ("Classpath", "Dept", "Class", "Fine", "Product Name",
                                    "MANUFACTURER_NAME", "BRAND_NAME", "Part_Manuf",
                                    "Standard/Approvals")}
            base["BRAND_NAME"] = brand
            base["Standard/Approvals"] = approvals
            return base

        rows = [
            row("Stable®", "UL Listed|RoHS Compliant"),
            row("Stable®", "UL Listed|RoHS Compliant"),
            row("Stable®", "UL Listed|RoHS Compliant"),
            row("Noisy®", "UL Listed"),
            row("Noisy®", "RoHS Compliant"),
            row("Noisy®", "CSA Certified"),
            row("Blank®", ""),
            row("Blank®", ""),
            row("Blank®", ""),
        ]
        kb = KnowledgeBase.fit(pd.DataFrame(rows))
        assert kb.brand_approvals == {"Stable®": ["RoHS Compliant", "UL Listed"]}
