"""Sourcing, digital assets and the packaging tail of the schema.

This stage fills the last third of the delivery format: the manufacturer URL,
image and document filenames, dimension columns and the per-brand legal text.
It is entirely deterministic — there is no LLM call here — because every value
it writes is either a naming convention, a measurement already extracted by the
attribute stage, or text copied verbatim from an approved row for the same
brand.

Nothing is written speculatively. A document filename is only emitted for a
brand the training fold shows publishes that document type, and the URL is the
manufacturer's own verified domain rather than a guessed product path. Every
value records its provenance so a reviewer can see it was derived, not found.
"""

from __future__ import annotations

from backend.core.normalize import clean, normalize_measure_text
from backend.core.schema import ProductRecord
from backend.knowledge.assets import (
    ALTERNATE_COLUMNS,
    DOCUMENT_COLUMNS,
    IMAGE_COLUMN,
    dimensions_from,
    document_label,
    slugify_asset,
)
from backend.knowledge.registry import KnowledgeBase
from backend.sourcing.policy import official_domain

# Attribute labels that carry an axis-labelled dimension string.
_SIZE_LABELS: tuple[str, ...] = ("size", "dimensions", "overall dimensions")
_WEIGHT_LABELS: tuple[str, ...] = ("weight", "net weight", "product weight")

# Only claim alternate images for a brand that reliably supplies them.
_ALTERNATE_THRESHOLD = 0.6


class SourcingAgent:
    """Fills MFR URL, asset filenames, dimensions and brand-level legal text."""

    name = "sourcing"

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    def run(self, record: ProductRecord) -> ProductRecord:
        brand = record.brand_name or ""
        manufacturer = record.manufacturer_name or ""

        self._sourcing_url(record, brand, manufacturer)
        self._assets(record, brand, manufacturer)
        self._dimensions(record)
        self._facts(record, brand)
        return record

    # -- manufacturer's own site -------------------------------------------
    def _sourcing_url(self, record: ProductRecord, brand: str, manufacturer: str) -> None:
        """Fall back to the approved domain when research found no deep link.

        The research stage sets `mfr_url` to a verified product page whenever it
        can confirm one, which is strictly better than a bare domain. This is the
        floor, not the default: a domain we know is right beats no URL at all,
        and it is never a distributor or a marketplace.
        """
        if record.mfr_url:
            return  # a retrieved, part-number-confirmed page already won
        domain = self.kb.assets.domain_for(brand, manufacturer)
        if not domain:
            # Fall back to the seeded official-domain registry so brands that
            # are known to the policy layer but absent from the training fold
            # still get a source URL rather than leaving the row unsourced.
            seeded = official_domain(brand, manufacturer)
            if seeded:
                domain = f"https://{seeded}"
        if not domain:
            return
        record.mfr_url = domain
        record.provenance["mfr_url"] = "approved-manufacturer-domain (no deep link found)"
        record.confidence["mfr_url"] = 0.7  # right source, unverified deep path

    # -- digital assets ----------------------------------------------------
    def _assets(self, record: ProductRecord, brand: str, manufacturer: str) -> None:
        part = slugify_asset(record.mpn or record.raw_mpn)
        prefix = self.kb.assets.prefix_for(brand, manufacturer)
        if not part or not prefix:
            return

        stem = f"{prefix}_{part}"

        if self.kb.assets.image_rate(brand) >= 0.5:
            record.extras[IMAGE_COLUMN] = f"{stem}.jpg"
            record.extras["Actual Image (Yes/No)"] = self.kb.assets.actual_image_value
            record.provenance["product_image"] = "naming-convention"

            for index, column in enumerate(ALTERNATE_COLUMNS, start=1):
                if self.kb.assets.publishes(brand, column, _ALTERNATE_THRESHOLD):
                    record.extras[column] = f"{stem}_{index}.jpg"

        for column in DOCUMENT_COLUMNS:
            if self.kb.assets.publishes(brand, column):
                record.extras[column] = f"{stem}_{document_label(column)}.pdf"
        if any(c in record.extras for c in DOCUMENT_COLUMNS):
            record.provenance["documents"] = "naming-convention"

    # -- dimensions --------------------------------------------------------
    def _dimensions(self, record: ProductRecord) -> None:
        """Split an axis-labelled size attribute into its own columns."""
        for attr in record.attributes:
            folded = attr.label.casefold()
            if not attr.is_filled:
                continue
            # A dimension already written from a retrieved first-party
            # specification is better evidenced than one parsed back out of a
            # description, so it is never overwritten here.
            if any(folded == label for label in _SIZE_LABELS):
                found = dimensions_from(attr.rendered())
                written = [c for c, v in found.items() if record.extras.setdefault(c, v) == v]
                if written:
                    record.provenance.setdefault(
                        "dimensions", f"parsed from {attr.label}"
                    )
            elif any(folded == label for label in _WEIGHT_LABELS):
                value = normalize_measure_text(attr.value or "")
                if value:
                    record.extras.setdefault("WEIGHT", value)
                    record.extras.setdefault("WEIGHT_UOM", clean(attr.uom) or "lb")

    # -- per-brand legal text ----------------------------------------------
    def _facts(self, record: ProductRecord, brand: str) -> None:
        for column in ("Warranty", "Country Of Origin", "Prop 65"):
            value = self.kb.assets.fact_for(brand, column)
            if value:
                record.extras[column] = value
                record.provenance[column] = "brand-level approved value"
