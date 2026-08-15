"""Manufacturer and brand resolution.

Distributor supplier strings are noisy and often useless: a co-op such as
"Appliance Dealers Cooperative (APPDE)" fronts six unrelated brands, and the
brand columns are usually placeholders. Resolution order is therefore:

1. exact brand-hint match against the approved brand list
2. fuzzy brand-hint match
3. supplier-string mapping, weighted by how consistently that supplier maps
4. LLM inference from the part number and description, then snapped back onto
   the approved list so symbols and legal suffixes stay exact
"""

from __future__ import annotations

from backend.core.normalize import clean
from backend.core.schema import ProductRecord
from backend.knowledge.registry import KnowledgeBase
from backend.llm.client import GeminiClient

SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string"},
        "manufacturer": {"type": "string"},
        "series": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["brand", "manufacturer", "confidence"],
}

SYSTEM = (
    "You identify the true manufacturer and brand of industrial products from "
    "part-number patterns and abbreviated descriptions. Distributor and "
    "buying-co-op names are NOT manufacturers."
)

# Below this, we ask the model rather than trusting the registry.
REGISTRY_TRUST_FLOOR = 0.75


class ManufacturerAgent:
    """Fills manufacturer_name, brand_name, mpn and series."""

    name = "manufacturer_resolver"

    def __init__(self, kb: KnowledgeBase, llm: GeminiClient) -> None:
        self.kb = kb
        self.llm = llm

    def run(self, record: ProductRecord) -> ProductRecord:
        record.mpn = clean(record.raw_mpn) or None
        if record.mpn:
            record.confidence["mpn"] = 1.0
            record.provenance["mpn"] = "passthrough"

        manufacturer, brand, confidence, provenance = self.kb.manufacturers.resolve(
            record.raw_manufacturer,
            record.brand_hints,
            mpn=record.raw_mpn,
            description=record.raw_description,
        )

        if brand and confidence >= REGISTRY_TRUST_FLOOR:
            record.manufacturer_name = manufacturer or None
            record.brand_name = brand
            record.confidence["brand_name"] = confidence
            record.confidence["manufacturer_name"] = confidence
            record.provenance["brand_name"] = provenance
            return record

        # Registry was ambiguous or empty - ask the model, seeded with the
        # approved options so it picks rather than invents.
        self._infer(record, fallback=(manufacturer, brand, confidence, provenance))
        return record

    def _infer(
        self,
        record: ProductRecord,
        fallback: tuple[str, str, float, str],
    ) -> None:
        known_brands = sorted(self.kb.manufacturers.brand_to_manufacturer)
        listed = "\n".join(f"- {b}" for b in known_brands[:80])

        prompt = (
            f"Product description: {record.raw_description!r}\n"
            f"Manufacturer part number: {record.raw_mpn!r}\n"
            f"Distributor supplier string: {record.raw_manufacturer!r}\n"
            f"Brand hints from source system: {record.brand_hints or 'none'}\n"
            f"Category: {record.dept} > {record.klass} > {record.fine}\n\n"
            f"Known approved brands (prefer one of these if it fits):\n{listed}\n\n"
            "Identify the real brand and its parent manufacturer company. "
            "If the supplier string is a distributor or buying co-op, ignore it. "
            "Also extract the product series if the description names one "
            "(e.g. 'Professional Series'), else empty string. "
            "confidence is 0..1."
        )
        result = self.llm.generate_json(prompt, SCHEMA, fast=True, system=SYSTEM)

        fb_manufacturer, fb_brand, fb_confidence, fb_provenance = fallback

        if not result:
            record.manufacturer_name = fb_manufacturer or None
            record.brand_name = fb_brand or None
            record.confidence["brand_name"] = fb_confidence
            record.confidence["manufacturer_name"] = fb_confidence
            record.provenance["brand_name"] = f"{fb_provenance} (llm unavailable)"
            if not fb_brand:
                record.issues.append("brand unresolved")
            return

        # Snap generated names onto their approved spellings so that ® / ™ and
        # legal suffixes match the master list exactly.
        raw_brand = clean(str(result.get("brand", "")))
        raw_manufacturer = clean(str(result.get("manufacturer", "")))
        brand = self.kb.manufacturers.canonical_brand(raw_brand) if raw_brand else ""
        manufacturer = (
            self.kb.manufacturers.canonical_manufacturer(raw_manufacturer)
            if raw_manufacturer
            else ""
        )

        # If the brand is on the approved list, prefer its recorded parent.
        if brand in self.kb.manufacturers.brand_to_manufacturer:
            manufacturer = self.kb.manufacturers.brand_to_manufacturer[brand]
            snapped = True
        else:
            snapped = False

        llm_confidence = float(result.get("confidence", 0.6) or 0.6)
        # An off-list brand is a real possibility, but trust it less.
        confidence = min(llm_confidence, 0.9 if snapped else 0.6)

        if brand:
            record.brand_name = brand
            record.manufacturer_name = manufacturer or fb_manufacturer or None
            record.confidence["brand_name"] = confidence
            record.confidence["manufacturer_name"] = confidence
            record.provenance["brand_name"] = (
                "llm-snapped" if snapped else "llm-offlist"
            )
            if not snapped:
                record.issues.append(f"brand {brand!r} not in approved list")
        else:
            record.brand_name = fb_brand or None
            record.manufacturer_name = fb_manufacturer or None
            record.confidence["brand_name"] = fb_confidence
            record.provenance["brand_name"] = fb_provenance
            record.issues.append("brand unresolved")

        if series := clean(str(result.get("series", ""))):
            if accepted := self.kb.plausible_series(record.classpath, series):
                record.series = accepted
                record.confidence["series"] = confidence
