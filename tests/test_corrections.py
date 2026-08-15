"""Tests for the reviewer correction store and its replay onto records."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.schema import Attribute, ProductRecord
from backend.knowledge.corrections import CorrectionStore


@pytest.fixture()
def store(tmp_path: Path) -> CorrectionStore:
    return CorrectionStore(tmp_path / "corrections.jsonl")


def _record(part_number: str = "12345") -> ProductRecord:
    return ProductRecord(
        part_number=part_number,
        brand_name="SATCO®",
        manufacturer_name="Satco Products Inc",
        classpath="Electrical > Lighting > Lamps",
        attributes=[
            Attribute(label="Wattage", value="60", uom="W", confidence=0.7, source="llm"),
            Attribute(label="Voltage", value="120", uom="V", confidence=0.7, source="llm"),
        ],
        needs_review=True,
    )


def test_record_and_get_roundtrip(store: CorrectionStore) -> None:
    store.record(
        "12345",
        status="corrected",
        fields={"brand_name": "NUVO® by SATCO"},
        attributes=[{"label": "Wattage", "value": "75", "uom": "W"}],
        notes="brand was wrong",
    )
    decision = store.get("12345")
    assert decision is not None
    assert decision["status"] == "corrected"
    assert decision["fields"] == {"brand_name": "NUVO® by SATCO"}
    assert decision["attributes"][0]["value"] == "75"
    assert store.status_of("12345") == "corrected"
    assert store.status_of("99999") == "pending"


def test_unknown_fields_are_dropped(store: CorrectionStore) -> None:
    decision = store.record(
        "12345", status="corrected", fields={"not_a_field": "x", "mpn": "S6601"}
    )
    assert decision["fields"] == {"mpn": "S6601"}


def test_invalid_status_rejected(store: CorrectionStore) -> None:
    with pytest.raises(ValueError):
        store.record("12345", status="maybe")


def test_persistence_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "corrections.jsonl"
    first = CorrectionStore(path)
    first.record("12345", status="approved")
    second = CorrectionStore(path)
    assert second.status_of("12345") == "approved"


def test_latest_decision_wins(store: CorrectionStore) -> None:
    store.record("12345", status="approved")
    store.record("12345", status="corrected", fields={"mpn": "S6601"})
    assert store.status_of("12345") == "corrected"
    assert store.get("12345")["fields"] == {"mpn": "S6601"}


def test_apply_overrides_fields_and_attributes(store: CorrectionStore) -> None:
    store.record(
        "12345",
        status="corrected",
        fields={"brand_name": "NUVO® by SATCO", "mpn": "S6601"},
        attributes=[{"label": "Wattage", "value": "75", "uom": "W"}],
        extras={"UPC": "045923660115"},
    )
    record = store.apply(_record())

    assert record.brand_name == "NUVO® by SATCO"
    assert record.mpn == "S6601"
    assert record.confidence["brand_name"] == 1.0
    assert record.provenance["brand_name"] == "reviewer-correction"

    wattage = record.attribute("Wattage")
    assert wattage is not None and wattage.value == "75"
    assert wattage.source == "reviewer-correction"

    # Untouched values stay exactly as the pipeline produced them.
    voltage = record.attribute("Voltage")
    assert voltage is not None and voltage.value == "120"

    assert record.extras["UPC"] == "045923660115"
    assert record.needs_review is False


def test_apply_appends_unknown_attribute(store: CorrectionStore) -> None:
    store.record(
        "12345",
        status="corrected",
        attributes=[{"label": "Color Temperature", "value": "3000", "uom": "K"}],
    )
    record = store.apply(_record())
    attr = record.attribute("Color Temperature")
    assert attr is not None and attr.value == "3000" and attr.uom == "K"


def test_apply_without_decision_is_noop(store: CorrectionStore) -> None:
    record = _record("99999")
    assert store.apply(record) is record
    assert record.needs_review is True


def test_summary_counts(store: CorrectionStore) -> None:
    store.record("1", status="approved")
    store.record("2", status="corrected", fields={"mpn": "A"}, attributes=[{"label": "X", "value": "1"}])
    summary = store.summary()
    assert summary["decisions"] == 2
    assert summary["approved"] == 1
    assert summary["corrected"] == 1
    assert summary["field_overrides"] == 1
    assert summary["attribute_overrides"] == 1
