"""Scoring the pipeline against known-good delivery rows.

Three families of metric, because they answer different questions:

* **Field accuracy** - did we produce the same value a human editor did?
  Reported as exact match and fuzzy match, since a description that differs by
  word order is not the same kind of error as one that is simply wrong.
* **Compliance** - does the output obey the house rules regardless of whether
  it matches ground truth? Character limits, casing, unit spacing, fractions.
  This is measurable on rows with no label at all.
* **Coverage** - how much of the schema did we actually populate? Completeness
  without accuracy is worthless, and accuracy without completeness is a
  cherry-pick, so both are reported.
* **Traceability** - how much of the output is backed by a document retrieved
  from the manufacturer's own site, and how many attribute values appear in that
  document verbatim? A separate question from accuracy: it asks whether a
  reviewer can check the claim, not whether the claim matched the label.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd
from rapidfuzz import fuzz

from backend.core.normalize import clean, repair_symbols
from backend.core.schema import CHAR_LIMITS, MAX_ATTRIBUTES

# Fields we claim to generate, so these are what we get judged on.
SCORED_FIELDS: tuple[str, ...] = (
    "MANUFACTURER_NAME",
    "BRAND_NAME",
    "MANUFACTURER_PART_NUMBER",
    "Classpath",
    "Product Name",
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
)

# Exact string equality is the right test for identity fields; descriptions are
# judged on fuzzy similarity, since valid phrasings differ.
EXACT_FIELDS: frozenset[str] = frozenset(
    {"MANUFACTURER_NAME", "BRAND_NAME", "MANUFACTURER_PART_NUMBER", "Classpath"}
)

FUZZY_PASS = 85


def _norm(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean(repair_symbols(str(value)))


def _cmp_key(value: str) -> str:
    """Case- and punctuation-insensitive key for fuzzy comparison."""
    return re.sub(r"[^a-z0-9 ]+", " ", value.casefold()).strip()


@dataclass
class FieldScore:
    field: str
    total: int = 0
    both_present: int = 0
    exact: int = 0
    fuzzy: int = 0
    predicted_present: int = 0
    truth_present: int = 0
    fuzzy_scores: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        denominator = self.both_present or 1
        return {
            "field": self.field,
            "compared": self.both_present,
            "exact_match": round(self.exact / denominator, 3),
            "fuzzy_match": round(self.fuzzy / denominator, 3),
            "mean_similarity": round(
                sum(self.fuzzy_scores) / len(self.fuzzy_scores) / 100, 3
            ) if self.fuzzy_scores else 0.0,
            "fill_rate": round(self.predicted_present / (self.total or 1), 3),
            "truth_fill_rate": round(self.truth_present / (self.total or 1), 3),
        }


class Evaluator:
    """Compares a predicted delivery frame against the ground-truth frame."""

    def __init__(self, key: str = "PART_NUMBER") -> None:
        self.key = key

    # -- accuracy --
    def score_fields(
        self, predicted: pd.DataFrame, truth: pd.DataFrame
    ) -> list[dict[str, Any]]:
        truth_indexed = truth.set_index(self.key)
        scores: dict[str, FieldScore] = {f: FieldScore(f) for f in SCORED_FIELDS}

        for _, row in predicted.iterrows():
            key = row.get(self.key)
            if key not in truth_indexed.index:
                continue
            truth_row = truth_indexed.loc[key]
            if isinstance(truth_row, pd.DataFrame):
                truth_row = truth_row.iloc[0]

            for name in SCORED_FIELDS:
                score = scores[name]
                score.total += 1
                got, want = _norm(row.get(name)), _norm(truth_row.get(name))
                if got:
                    score.predicted_present += 1
                if want:
                    score.truth_present += 1
                if not (got and want):
                    continue

                score.both_present += 1
                if got == want:
                    score.exact += 1
                    score.fuzzy += 1
                    score.fuzzy_scores.append(100.0)
                    continue

                similarity = fuzz.token_set_ratio(_cmp_key(got), _cmp_key(want))
                score.fuzzy_scores.append(float(similarity))
                if name in EXACT_FIELDS:
                    # Identity fields only pass on a near-perfect match.
                    if similarity >= 97:
                        score.fuzzy += 1
                elif similarity >= FUZZY_PASS:
                    score.fuzzy += 1

        return [scores[f].as_dict() for f in SCORED_FIELDS]

    # -- attribute-level accuracy --
    def score_attributes(
        self, predicted: pd.DataFrame, truth: pd.DataFrame
    ) -> dict[str, Any]:
        """Label and value agreement across all attribute triplets."""
        truth_indexed = truth.set_index(self.key)
        label_hits = label_total = 0
        value_hits = value_compared = 0

        for _, row in predicted.iterrows():
            key = row.get(self.key)
            if key not in truth_indexed.index:
                continue
            truth_row = truth_indexed.loc[key]
            if isinstance(truth_row, pd.DataFrame):
                truth_row = truth_row.iloc[0]

            truth_pairs: dict[str, str] = {}
            for i in range(1, MAX_ATTRIBUTES + 1):
                label = _norm(truth_row.get(f"ATTRIBUTE_LABEL {i}"))
                if label:
                    truth_pairs[label.casefold()] = _norm(
                        truth_row.get(f"ATTRIBUTE_VALUE {i}")
                    )

            for i in range(1, MAX_ATTRIBUTES + 1):
                label = _norm(row.get(f"ATTRIBUTE_LABEL {i}"))
                if not label:
                    continue
                label_total += 1
                folded = label.casefold()
                if folded not in truth_pairs:
                    continue
                label_hits += 1
                got, want = _norm(row.get(f"ATTRIBUTE_VALUE {i}")), truth_pairs[folded]
                if got and want:
                    value_compared += 1
                    if got == want or fuzz.ratio(_cmp_key(got), _cmp_key(want)) >= 90:
                        value_hits += 1

        return {
            "labels_emitted": label_total,
            "label_precision": round(label_hits / (label_total or 1), 3),
            "values_compared": value_compared,
            "value_accuracy": round(value_hits / (value_compared or 1), 3),
        }

    # -- compliance (needs no ground truth) --
    def score_compliance(self, predicted: pd.DataFrame) -> dict[str, Any]:
        total = len(predicted) or 1
        invoice_ok = invoice_caps = mobile_ok = 0
        unit_ok = fraction_ok = 0

        for _, row in predicted.iterrows():
            invoice = _norm(row.get("INVOICE_DESC"))
            if invoice and len(invoice) <= CHAR_LIMITS["INVOICE_DESC"][1]:
                invoice_ok += 1
            if invoice and invoice == invoice.upper():
                invoice_caps += 1

            mobile = _norm(row.get("MOBILE_DESC"))
            low, high = CHAR_LIMITS["MOBILE_DESC"]
            if mobile and low <= len(mobile) <= high:
                mobile_ok += 1

            blob = " ".join(
                _norm(row.get(f))
                for f in ("SHORT_DESC", "LONG_DESC1", "RETAIL_DESC", "MOBILE_DESC")
            )
            # `24in` is a violation; `24 in` is correct.
            if not re.search(r"\d(in|ft|mm|cm|lb|oz|psi)\b", blob):
                unit_ok += 1
            # A decimal inch should have become a fraction.
            if not re.search(r"\d+\.\d+\s*in\b", blob):
                fraction_ok += 1

        return {
            "invoice_within_40_chars": round(invoice_ok / total, 3),
            "invoice_all_caps": round(invoice_caps / total, 3),
            "mobile_within_60_80_chars": round(mobile_ok / total, 3),
            "unit_spacing_correct": round(unit_ok / total, 3),
            "fractions_not_decimals": round(fraction_ok / total, 3),
        }

    # -- coverage --
    def score_coverage(self, predicted: pd.DataFrame, truth: pd.DataFrame) -> dict[str, Any]:
        """How much of what a human filled did we also fill?"""
        ours = theirs = 0
        for column in predicted.columns:
            if column not in truth.columns:
                continue
            ours += int(predicted[column].map(lambda v: bool(_norm(v))).sum())
            theirs += int(truth[column].map(lambda v: bool(_norm(v))).sum())
        return {
            "cells_filled": ours,
            "truth_cells_filled": theirs,
            "fill_ratio_vs_truth": round(ours / (theirs or 1), 3),
        }

    # -- traceability (needs no ground truth) --
    def score_sourcing(self, records: Sequence[Any]) -> dict[str, Any]:
        """How much of the output is backed by a first-party source.

        Distinct from accuracy, and not a substitute for it. Accuracy asks
        whether we matched the human; this asks whether a reviewer can check the
        claim. A value that is wrong but cited is auditable; a value that is
        right but unsourced still has to be trusted.

        `grounded_values` counts attribute values found verbatim in a document
        retrieved from the manufacturer's own site, so it is a floor on
        traceability rather than an estimate: the check is string containment
        against text we actually fetched.
        """
        total = len(records) or 1
        sourced = sum(1 for r in records if getattr(r, "citations", None))
        documents = sum(len(getattr(r, "citations", []) or []) for r in records)
        confirmed = sum(
            1
            for r in records
            if any(c.get("kind") != "editorial" for c in (getattr(r, "citations", []) or []))
        )
        filled = sum(len(r.filled_attributes()) for r in records)
        grounded = sum(len(getattr(r, "grounded", {}) or {}) for r in records)
        deep_links = sum(
            1
            for r in records
            if (getattr(r, "mfr_url", "") or "").count("/") > 2
        )
        kinds: Counter = Counter()
        third_party_documents = 0
        records_with_third_party = 0
        for record in records:
            citations = getattr(record, "citations", []) or []
            has_third_party = False
            for citation in citations:
                kinds[citation.get("kind", "other")] += 1
                if citation.get("source") == "third-party":
                    third_party_documents += 1
                    has_third_party = True
            if has_third_party:
                records_with_third_party += 1

        return {
            "records": len(records),
            "records_with_a_source": sourced,
            "sourced_rate": round(sourced / total, 3),
            "records_with_verified_source": confirmed,
            "documents_read": documents,
            "documents_by_kind": dict(kinds),
            # Fallback reach: records the manufacturer published nothing about,
            # supplemented from the reputable third-party allowlist.
            "third_party_documents": third_party_documents,
            "records_supplemented_third_party": records_with_third_party,
            "deep_product_links": deep_links,
            "deep_link_rate": round(deep_links / total, 3),
            "filled_attribute_values": filled,
            "grounded_values": grounded,
            # Share of filled attribute values traceable to a retrieved source.
            "grounded_rate": round(grounded / filled, 3) if filled else 0.0,
        }

    # -- everything --
    def evaluate(
        self,
        predicted: pd.DataFrame,
        truth: pd.DataFrame,
        records: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Score a run. `records` is optional and adds the traceability block."""
        fields = self.score_fields(predicted, truth)
        compared = [f for f in fields if f["compared"]]
        payload = {
            "rows": len(predicted),
            "fields": fields,
            "headline": {
                "mean_exact_match": round(
                    sum(f["exact_match"] for f in compared) / (len(compared) or 1), 3
                ),
                "mean_fuzzy_match": round(
                    sum(f["fuzzy_match"] for f in compared) / (len(compared) or 1), 3
                ),
            },
            "attributes": self.score_attributes(predicted, truth),
            "compliance": self.score_compliance(predicted),
            "coverage": self.score_coverage(predicted, truth),
        }
        if records is not None:
            payload["sourcing"] = self.score_sourcing(records)
        return payload
