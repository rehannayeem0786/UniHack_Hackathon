"""Validation, normalisation and confidence scoring.

This agent runs last and is purely deterministic. It enforces the house rules
that the guidelines specify and that a language model cannot be trusted to
follow reliably:

* character limits per field
* ALL CAPS on the invoice line
* one space between number and unit (`24 in`, never `24in`)
* fractions rather than decimals for inches
* attribute values present in the controlled vocabulary
* brand and manufacturer names matching the approved list exactly

Every violation it cannot silently repair becomes an issue on the record and
pushes the row towards `needs_review`, which is the human-in-the-loop signal.
"""

from __future__ import annotations

import re

from backend.core.normalize import (
    abbreviate_for_invoice,
    clean,
    normalize_measure_text,
    truncate_clean,
)
from backend.core.schema import CHAR_LIMITS, ProductRecord
from backend.knowledge.registry import KnowledgeBase

# A row below this overall score is flagged for a human to look at.
REVIEW_THRESHOLD = 0.65

# Fields that must never contain a bare decimal inch or an unspaced unit.
_TEXT_FIELDS = (
    "long_desc", "short_desc", "retail_desc", "mobile_desc", "marketing_desc",
)

# Weights for the overall confidence score: identity matters most, because a
# wrong brand makes every downstream field wrong too.
_WEIGHTS = {
    "classpath": 0.25,
    "brand_name": 0.20,
    "manufacturer_name": 0.10,
    "attributes": 0.25,
    "product_name": 0.10,
    "long_desc": 0.05,
    "mobile_desc": 0.05,
}


class ValidatorAgent:
    """Normalises output, records violations, and scores confidence."""

    name = "validator"

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    def run(self, record: ProductRecord) -> ProductRecord:
        self._normalise_text(record)
        self._enforce_limits(record)
        self._check_identity(record)
        self._check_vocabulary(record)
        self._score(record)
        return record

    # -- normalisation --
    def _normalise_text(self, record: ProductRecord) -> None:
        for field in _TEXT_FIELDS:
            value = getattr(record, field, None)
            if value:
                setattr(record, field, normalize_measure_text(value))

        for attr in record.attributes:
            if attr.is_filled:
                attr.value = normalize_measure_text(str(attr.value))

    # -- limits --
    def _enforce_limits(self, record: ProductRecord) -> None:
        # Invoice line: ALL CAPS and hard 40-char ceiling.
        if record.invoice_desc:
            fixed = abbreviate_for_invoice(record.invoice_desc, CHAR_LIMITS["INVOICE_DESC"][1])
            if fixed != record.invoice_desc:
                record.provenance["invoice_desc"] = "formula+abbreviated"
            record.invoice_desc = fixed
            if len(fixed) > CHAR_LIMITS["INVOICE_DESC"][1]:
                record.issues.append("INVOICE_DESC exceeds 40 chars")
            if fixed != fixed.upper():
                record.invoice_desc = fixed.upper()

        # Mobile line: 60-80 chars. Over is repairable, under is reported.
        if record.mobile_desc:
            low, high = CHAR_LIMITS["MOBILE_DESC"]
            if len(record.mobile_desc) > high:
                record.mobile_desc = truncate_clean(record.mobile_desc, high)
            if len(record.mobile_desc) < low:
                record.issues.append(
                    f"MOBILE_DESC is {len(record.mobile_desc)} chars, below the {low} minimum"
                )

    # -- identity --
    def _check_identity(self, record: ProductRecord) -> None:
        if not record.classpath:
            record.issues.append("missing Classpath")
        if not record.brand_name:
            record.issues.append("missing BRAND_NAME")
        if not record.manufacturer_name:
            record.issues.append("missing MANUFACTURER_NAME")
        if not record.mpn:
            record.issues.append("missing MANUFACTURER_PART_NUMBER")

        # Snap identity fields onto the approved spellings one final time, so
        # ® / ™ and legal suffixes are exact.
        if record.brand_name:
            canonical = self.kb.manufacturers.canonical_brand(record.brand_name)
            if canonical and canonical != record.brand_name:
                record.provenance["brand_name"] = (
                    record.provenance.get("brand_name", "") + "+canonicalised"
                )
                record.brand_name = canonical

        if record.manufacturer_name:
            canonical = self.kb.manufacturers.canonical_manufacturer(record.manufacturer_name)
            if canonical:
                record.manufacturer_name = canonical

        # The brand/manufacturer pair should agree with the master list.
        known = self.kb.manufacturers.brand_to_manufacturer
        if record.brand_name in known and record.manufacturer_name:
            expected = known[record.brand_name]
            if expected != record.manufacturer_name:
                record.issues.append(
                    f"brand/manufacturer mismatch: {record.brand_name} normally maps "
                    f"to {expected}, got {record.manufacturer_name}"
                )

    # -- vocabulary --
    def _check_vocabulary(self, record: ProductRecord) -> None:
        if not record.classpath:
            return
        for attr in record.attributes:
            if not attr.is_filled:
                continue
            if self.kb.attributes.is_free_text(record.classpath, attr.label):
                continue
            permitted = self.kb.attributes.permitted(record.classpath, attr.label, limit=500)
            if permitted and str(attr.value) not in permitted:
                record.issues.append(
                    f"{attr.label}={attr.value!r} is outside the controlled vocabulary"
                )
                attr.confidence = min(attr.confidence, 0.5)

            uom_expected = self.kb.attributes.expected_uom(record.classpath, attr.label)
            if uom_expected and attr.uom and attr.uom != uom_expected:
                record.issues.append(
                    f"{attr.label} unit {attr.uom!r} differs from approved {uom_expected!r}"
                )

        # Nothing anywhere should still contain a decimal inch.
        for field in _TEXT_FIELDS:
            value = getattr(record, field, None) or ""
            if re.search(r"\d+\.\d+\s*in\b", value):
                record.issues.append(f"{field} still contains a decimal inch value")

    # -- scoring --
    def _score(self, record: ProductRecord) -> None:
        total = weight_sum = 0.0
        for field, weight in _WEIGHTS.items():
            if field in record.confidence:
                total += record.confidence[field] * weight
                weight_sum += weight

        overall = total / weight_sum if weight_sum else 0.0

        # Unresolved issues erode the score; identity problems erode it hard.
        severe = sum(
            1 for i in record.issues
            if "missing" in i or "mismatch" in i or "outside" in i
        )
        overall *= max(0.4, 1.0 - 0.08 * severe)

        record.confidence["overall"] = round(overall, 3)
        record.needs_review = overall < REVIEW_THRESHOLD or severe >= 3
        if record.needs_review and not record.issues:
            record.issues.append("low overall confidence")
