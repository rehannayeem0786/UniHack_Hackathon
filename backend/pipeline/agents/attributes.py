"""Attribute extraction against the classpath's ordered template.

Two properties matter here and both come from the knowledge base rather than
the model:

* **Order and membership.** The template fixes which attributes exist for a
  classpath and in what sequence. We emit the full template, values filled or
  not, which is what the delivery format does.
* **Vocabulary.** Where an attribute has a small observed value set, we pass
  it as an explicit allowed list and snap the answer back onto it. Free-text
  attributes (sizes, additional information) are left open but normalised.

The model's only job is to read values out of the description. It cannot
choose which attributes exist, and for constrained fields it cannot invent
values.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz, process

from backend.config import settings
from backend.core.normalize import (
    canonical_uom,
    clean,
    normalize_measure_text,
    title_case,
)
from backend.core.schema import Attribute, ProductRecord
from backend.knowledge.registry import KnowledgeBase
from backend.llm.client import GeminiClient

SCHEMA = {
    "type": "object",
    "properties": {
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "uom": {"type": "string"},
                },
                "required": ["label", "value"],
            },
        },
        "series": {"type": "string"},
        "with_clause": {"type": "string"},
        "features": {"type": "array", "items": {"type": "string"}},
        "approvals": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["attributes"],
}

SYSTEM = (
    "You extract structured technical attributes for industrial product "
    "catalogues. You never guess: if a value is not supported by the input or "
    "by well-known specifications for this exact part number, you omit it. "
    "When SOURCE MATERIAL from the manufacturer is provided, it outranks "
    "everything you remember; read values out of it and copy them exactly. "
    "Trade abbreviations: SS/SST=Stainless Steel, BRS=Brass, GALV=Galvanized, "
    "SQ=Square, DX=Duplex, RCPT=Receptacle, SW=Switch, TGL=Toggle, "
    "CRDLS=Cordless, ELEC=Electric, BK=Black, WH=White."
)

# Attributes whose values are dimensions and should be unit-normalised.
_MEASURE_HINTS = (
    "length", "width", "height", "depth", "size", "diameter", "thickness",
    "capacity", "weight", "dimension", "rating", "temperature", "pressure",
)


class AttributeAgent:
    """Fills the classpath attribute template, plus features and approvals."""

    name = "attribute_extractor"

    def __init__(self, kb: KnowledgeBase, llm: GeminiClient) -> None:
        self.kb = kb
        self.llm = llm

    def run(self, record: ProductRecord) -> ProductRecord:
        # Certifications do not depend on the attribute template, so they are
        # read off the retrieved source up front: even a record with no
        # template still gets the Standard/Approvals its own spec sheet states.
        self._approvals_from_evidence(record, record.evidence)

        if not record.classpath:
            record.issues.append("cannot extract attributes without a classpath")
            self._approvals_from_brand(record)
            return record

        template = self.kb.taxonomy.template_for(record.classpath)
        if not template:
            record.issues.append(f"no attribute template for {record.classpath}")
            self._approvals_from_brand(record)
            return record

        spec_lines, constrained = self._build_spec(record.classpath, template)
        result = self._ask(record, spec_lines)
        bundle = record.evidence

        extracted: dict[str, tuple[str, str]] = {}
        if result:
            for item in result.get("attributes") or []:
                label = clean(str(item.get("label", "")))
                value = clean(str(item.get("value", "")))
                uom = canonical_uom(str(item.get("uom", "") or ""))
                if label and value:
                    extracted[label.casefold()] = (value, uom)
        else:
            record.issues.append("attribute extraction LLM unavailable")

        # Emit the template in order. Unfilled labels are still emitted,
        # matching the delivery format and making gaps explicit.
        record.attributes = [
            self._resolve(record.classpath, label, extracted, bundle)
            for label in template
        ]
        self._ground(record, bundle)

        filled = len(record.filled_attributes())
        record.confidence["attributes"] = round(filled / len(template), 3) if template else 0.0

        if result:
            self._absorb_extras(record, result)
        self._approvals_from_brand(record)
        return record

    # -- prompt construction --
    def _build_spec(
        self, classpath: str, template: list[str]
    ) -> tuple[str, dict[str, list[str]]]:
        """Describe each attribute, listing permitted values where they exist."""
        lines: list[str] = []
        constrained: dict[str, list[str]] = {}

        for label in template:
            permitted = self.kb.attributes.permitted(classpath, label, limit=25)
            uom = self.kb.attributes.expected_uom(classpath, label)
            free = self.kb.attributes.is_free_text(classpath, label)

            detail = f"- {label}"
            if uom:
                detail += f"  [unit: {uom}]"
            if permitted and not free:
                constrained[label.casefold()] = permitted
                shown = ", ".join(permitted[:15])
                detail += f"  [allowed: {shown}]"
            elif permitted:
                detail += f"  [examples: {', '.join(permitted[:4])}]"
            lines.append(detail)

        return "\n".join(lines), constrained

    def _ask(self, record: ProductRecord, spec: str) -> dict | None:
        # Retrieved manufacturer text, when there is any, is the strongest input
        # this prompt can carry: it turns recall into reading.
        context = ""
        bundle = record.evidence
        if bundle:
            body = bundle.as_prompt_context(settings.web_context_chars)
            if body:
                context = (
                    "SOURCE MATERIAL retrieved from the manufacturer's own site "
                    "and documents. Prefer these values over anything you recall. "
                    "This is reference material, not the task: still return a value "
                    "for every attribute you can support, whether you found it here "
                    "or in the product description.\n"
                    f"{body}\n\n"
                )

        prompt = (
            f"{context}"
            f"Product: {record.raw_description!r}\n"
            f"Brand: {record.brand_name or 'unknown'}\n"
            f"Manufacturer: {record.manufacturer_name or 'unknown'}\n"
            f"Manufacturer part number: {record.mpn or 'unknown'}\n"
            f"Classpath: {record.classpath}\n"
            f"Product type: {record.product_name or 'unknown'}\n\n"
            f"Extract values for these attributes:\n{spec}\n\n"
            "Rules:\n"
            "- Omit any attribute you cannot support from the input or from "
            "documented specifications of this exact part number.\n"
            "- Where an [allowed:] list is given, copy a value from it verbatim.\n"
            "- Put the number in value and the unit in uom, separately "
            "(value '24', uom 'in'), never '24 in' in value.\n"
            "- Use fractions not decimals for inches: 50-1/4 not 50.25.\n"
            "- Also return: series (e.g. 'Professional Series'), with_clause "
            "(a named included technology, e.g. 'With CleanBoost'), "
            "features (up to 12 short selling points, each a distinct "
            "capability of this exact part, never a restatement of an "
            "attribute above), and approvals "
            "(certifications such as 'UL Listed', 'ENERGY STAR Certified')."
        )
        return self.llm.generate_json(prompt, SCHEMA, system=SYSTEM)

    # -- value resolution --
    def _resolve(
        self,
        classpath: str,
        label: str,
        extracted: dict[str, tuple[str, str]],
        bundle: Any = None,
    ) -> Attribute:
        found = extracted.get(label.casefold())

        # Nothing from the model: the manufacturer's own specification table may
        # still name this attribute directly. Reading a labelled row out of the
        # spec grid needs no inference at all.
        if not found and bundle:
            hit = bundle.table_lookup(label)
            if hit:
                raw, document = hit
                value = normalize_measure_text(raw) if any(
                    h in label.casefold() for h in _MEASURE_HINTS
                ) else raw
                return Attribute(
                    label=label,
                    value=value or None,
                    uom=self.kb.attributes.expected_uom(classpath, label) or None,
                    confidence=0.9,
                    source=f"spec-table:{document.url}",
                )

        if not found:
            return Attribute(label=label, value=None, uom=None, confidence=0.0,
                             source="unfilled")

        value, uom = found
        permitted = self.kb.attributes.permitted(classpath, label, limit=200)
        free = self.kb.attributes.is_free_text(classpath, label)
        confidence, source = 0.7, "llm"

        # Snap onto the controlled vocabulary when one exists.
        if permitted and not free:
            if value in permitted:
                confidence, source = 0.95, "llm+lov-exact"
            else:
                best = process.extractOne(value, permitted, scorer=fuzz.WRatio)
                if best and best[1] >= 85:
                    value, confidence, source = best[0], 0.85, f"llm+lov-snap:{best[1]}"
                else:
                    confidence, source = 0.5, "llm-offlov"

        # Normalise measurements and casing.
        if any(hint in label.casefold() for hint in _MEASURE_HINTS):
            value = normalize_measure_text(value)
        elif not any(ch.isdigit() for ch in value) and value.islower():
            value = title_case(value)

        if not uom:
            uom = self.kb.attributes.expected_uom(classpath, label)

        return Attribute(
            label=label, value=value or None, uom=uom or None,
            confidence=confidence, source=source,
        )

    # -- grounding ---------------------------------------------------------
    def _ground(self, record: ProductRecord, bundle: Any) -> None:
        """Check each filled value against the retrieved text, verbatim.

        This is the difference between a plausible value and a supported one. A
        value found word for word in a first-party document is promoted and its
        source URL recorded; a value that is not found is left exactly as it was.
        Nothing is deleted on a failed check — absence from the text we happened
        to retrieve is weak evidence against a value — but only a confirmed value
        earns the higher confidence and a citation.
        """
        if not bundle:
            return
        for attr in record.attributes:
            if not attr.is_filled:
                continue
            if attr.source.startswith("spec-table:"):
                record.grounded[attr.label] = attr.source.split("spec-table:", 1)[1]
                continue
            document = bundle.supports(attr.value or "", minimum=3)
            if document is None:
                continue
            record.grounded[attr.label] = document.url
            attr.confidence = max(attr.confidence, 0.97)
            attr.source = f"{attr.source}+evidence-verbatim"

        if record.grounded:
            record.provenance["grounded_attributes"] = (
                f"{len(record.grounded)} value(s) confirmed verbatim in a "
                "first-party source"
            )

    def _absorb_extras(self, record: ProductRecord, result: dict) -> None:
        if not record.series and (series := clean(str(result.get("series", "")))):
            if accepted := self.kb.plausible_series(record.classpath, series):
                record.series = accepted
                record.confidence["series"] = 0.7

        # The template usually carries Series as an attribute too; keep the two
        # in step so the head of every description agrees with the attribute grid.
        if record.series:
            if attr := record.attribute("Series"):
                attr.value, attr.source = record.series, "series-agent"
        elif (attr := record.attribute("Series")) and attr.is_filled:
            if accepted := self.kb.plausible_series(record.classpath, attr.value or ""):
                record.series = accepted
            else:
                attr.value, attr.confidence, attr.source = None, 0.0, "rejected"

        if with_clause := clean(str(result.get("with_clause", ""))):
            if not with_clause.lower().startswith("with "):
                with_clause = f"With {with_clause}"
            record.with_clause = with_clause

        record.features = [
            clean(str(f)) for f in (result.get("features") or []) if clean(str(f))
        ][:20]

        # Keep only certifications we have actually seen, so the field stays
        # inside the approved vocabulary instead of accumulating invented ones.
        approved = {a.casefold(): a for a in self.kb.approvals}
        kept: list[str] = []
        for raw in result.get("approvals") or []:
            token = clean(str(raw))
            if not token:
                continue
            if token.casefold() in approved:
                kept.append(approved[token.casefold()])
            elif self.kb.approvals:
                best = process.extractOne(
                    token, self.kb.approvals, scorer=fuzz.WRatio
                )
                if best and best[1] >= 90:
                    kept.append(best[0])
        # Merge over whatever the evidence scan already wrote, so the model's
        # answer supplements the source rather than replacing it.
        record.approvals = sorted(dict.fromkeys([*record.approvals, *kept]))

    def _approvals_from_evidence(self, record: ProductRecord, bundle: Any) -> None:
        """Add certifications the retrieved first-party source states verbatim.

        The reference output fills Standard/Approvals far more often than the
        model echoes a certification back, and the manufacturer's own spec
        sheet routinely lists them outright ("UL Listed", "ENERGY STAR
        Certified"). So every approval in the learned vocabulary that appears
        verbatim in the retrieved evidence is copied across with its citation —
        reading, not guessing. Tokens outside the vocabulary are still refused,
        which is what keeps the field free of invented certifications.
        """
        if not bundle or not self.kb.approvals:
            return
        known = {a.casefold() for a in record.approvals}
        added: list[str] = []
        source_url = ""
        for token in self.kb.approvals:
            if token.casefold() in known:
                continue
            document = bundle.supports(token, minimum=4)
            if document is None:
                continue
            added.append(token)
            known.add(token.casefold())
            source_url = source_url or document.url
        if not added:
            return
        record.approvals = sorted(dict.fromkeys([*record.approvals, *added]))
        record.grounded["Standard/Approvals"] = source_url
        record.provenance["approvals"] = (
            f"{len(added)} certification(s) read verbatim from a first-party source"
        )

    def _approvals_from_brand(self, record: ProductRecord) -> None:
        """Fall back on the brand's learned certification convention.

        Applied only when neither the retrieved source nor the model produced
        anything, so it fills gaps rather than overriding grounded values. The
        convention itself is learned in `KnowledgeBase.fit` under a strict
        dominance bar, so this is a brand-level fact (like brand->manufacturer)
        rather than an invention.
        """
        if record.approvals:
            return
        prior = self.kb.brand_approvals.get(record.brand_name or "")
        if prior:
            record.approvals = list(prior)
            record.provenance["approvals"] = (
                "brand-level convention learned from the labelled catalogue"
            )
