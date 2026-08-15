"""Description building at five lengths, driven by the learned grammar.

Each surface is assembled from a head (identity) and a body (specification).
The body order and the way each value is written come from
`DescriptionStyleRegistry`, mined from the training fold, so the same code
produces `4 in Length` for an electrical box cover's long description and
`4 in L` for its retail line without either rule being hardcoded.

The head patterns are stable across the catalogue and read directly off the
delivery format:

    LONG_DESC1   Brand + Type [+ With], Series, <all attributes>,
                 Additional Information: ...
    SHORT_DESC   Brand + Series + MPN + Type [+ With], <key attributes>
    RETAIL_DESC  Series + Type, <key attributes>          (no brand, no MPN)
    MOBILE_DESC  Manufacturer + Brand, Type, [Series,] MPN, <padding> (60-80)
    INVOICE_DESC noun-first reversed type + abbreviated values, CAPS, <=40

Every value placed in a description has already passed the attribute stage's
LOV check, so a fluent sentence made of invented values is structurally
impossible. Only MARKETING_DESCRIPTION is free prose, and it is handed the same
validated attribute set and told to use nothing else.
"""

from __future__ import annotations

import re

from backend.core.normalize import (
    abbreviate_for_invoice,
    clean,
    strip_symbols,
    truncate_clean,
)
from backend.core.schema import ProductRecord
from backend.knowledge.registry import KnowledgeBase
from backend.llm.client import GeminiClient

MARKETING_SCHEMA = {
    "type": "object",
    "properties": {"marketing_description": {"type": "string"}},
    "required": ["marketing_description"],
}

MOBILE_MIN, MOBILE_MAX = 60, 80
INVOICE_MAX = 40

_ADDITIONAL = "additional information"

# `120 V` -> `120V` on the invoice line, which has no room for the space.
_GLUE_UNIT_RE = re.compile(
    r"(\d)\s+(IN|FT|MM|CM|LB|OZ|KG|GAL|PSI|V|A|W|HZ|DBA|DB|RPM|AWG|GA|HR|LM|K|AH|MAH)\b"
)


class DescriptionAgent:
    """Generates all six description fields."""

    name = "description_builder"

    def __init__(self, kb: KnowledgeBase, llm: GeminiClient) -> None:
        self.kb = kb
        self.llm = llm

    def run(self, record: ProductRecord) -> ProductRecord:
        item_type = clean(record.product_name) or self._type_from_classpath(record)
        record.product_name = item_type or None

        record.long_desc = self._long(record, item_type)
        record.short_desc = self._short(record, item_type)
        record.retail_desc = self._retail(record, item_type)
        record.mobile_desc = self._mobile(record, item_type)
        record.invoice_desc = self._invoice(record, item_type)
        record.marketing_desc = self._marketing(record, item_type)

        for field in (
            "long_desc", "short_desc", "retail_desc", "mobile_desc", "invoice_desc"
        ):
            record.confidence[field] = 0.9 if getattr(record, field) else 0.0
            record.provenance[field] = "formula+learned-style"
        return record

    # -- helpers ------------------------------------------------------------
    def _type_from_classpath(self, record: ProductRecord) -> str:
        if not record.classpath:
            return ""
        leaf = record.classpath.split(">")[-1].strip()
        # "Built-In Dishwashers" -> "Built-In Dishwasher"
        return leaf[:-1] if leaf.endswith("s") and not leaf.endswith("ss") else leaf

    def _body(self, record: ProductRecord, surface: str) -> list[str]:
        """Rendered attribute segments for a surface, in the learned order."""
        style = self.kb.style.style(record.classpath, surface)
        template = [a.label for a in record.attributes]
        order = self.kb.style.build_order(record.classpath, surface, template)

        available = {
            a.label.casefold(): a for a in record.attributes if a.is_filled
        }
        mpn = (record.mpn or "").casefold()

        segments: list[str] = []
        for label in order:
            if label == _ADDITIONAL:
                continue  # written as a tail clause, not a body segment
            attr = available.get(label)
            if not attr:
                continue

            # The part number already leads three of the five surfaces; repeating
            # it as a Model value reads as a stutter.
            if mpn and (attr.value or "").casefold() == mpn:
                continue

            rendered = style.render(label, attr.rendered())
            if not rendered or rendered in segments:
                continue
            # A bare number with no unit and no label is noise: "GFCI Outlet, 2,
            # 3, White" tells a buyer nothing. On the surface that learned a
            # suffix it reads correctly as "2 Poles, 3 Wires", so this only drops
            # it where the category gives it no meaning.
            if _is_bare_number(rendered):
                continue
            segments.append(rendered)
        return segments

    def _additional(self, record: ProductRecord) -> str:
        attr = record.attribute("Additional Information")
        return attr.rendered() if attr and attr.is_filled else ""

    def _join(self, head: str, segments: list[str]) -> str:
        parts = [head] if head else []
        parts.extend(s for s in segments if s)
        return clean(", ".join(parts))

    # -- the five formulas --------------------------------------------------
    def _long(self, record: ProductRecord, item_type: str) -> str | None:
        """Brand + Type + With, Series, every attribute, Additional Information."""
        head = " ".join(
            p for p in (record.brand_name, item_type, record.with_clause) if p
        ).strip()
        segments: list[str] = []
        if record.series:
            segments.append(record.series)
        segments.extend(self._body(record, "long"))

        text = self._join(head, segments)
        if extra := self._additional(record):
            text = f"{text}, Additional Information: {extra}" if text else extra
        return clean(text) or None

    def _short(self, record: ProductRecord, item_type: str) -> str | None:
        """Brand + Series + MPN + Type + With, then the key attributes."""
        head = " ".join(
            p
            for p in (
                record.brand_name, record.series, record.mpn, item_type,
                record.with_clause,
            )
            if p
        ).strip()
        return self._join(head, self._body(record, "short")) or None

    def _retail(self, record: ProductRecord, item_type: str) -> str | None:
        """Series + Type, then the key attributes. No brand, no part number."""
        head = " ".join(p for p in (record.series, item_type) if p).strip()
        return self._join(head, self._body(record, "retail")) or None

    def _mobile(self, record: ProductRecord, item_type: str) -> str | None:
        """60-80 characters. Symbols stripped, manufacturer and brand deduplicated."""
        head = self._mobile_head(record)
        lead = [p for p in (head, item_type, record.series, record.mpn) if clean(p)]
        if not lead:
            return None

        text = clean(", ".join(lead))
        if len(text) > MOBILE_MAX:
            # Drop the series, then the manufacturer half of the head, to fit.
            for trimmed in (
                [head, item_type, record.mpn],
                [strip_symbols(record.brand_name or ""), item_type, record.mpn],
            ):
                candidate = clean(", ".join(p for p in trimmed if clean(p)))
                if len(candidate) <= MOBILE_MAX:
                    text = candidate
                    break

        # Pad toward the 60-character floor. Preference goes to the attributes
        # this category actually puts on the mobile surface, but the 60-char
        # minimum is a hard rule, so anything filled is fair padding after that.
        if len(text) < MOBILE_MIN:
            preferred = self._body(record, "mobile")
            spare = [s for s in self._body(record, "long") if s not in preferred]
            for extra in preferred + spare:
                if extra in text:
                    continue
                for form in (extra, _glue_units(extra)):
                    candidate = f"{text}, {form}"
                    if len(candidate) <= MOBILE_MAX:
                        text = candidate
                        break
                if len(text) >= MOBILE_MIN:
                    break

        # Still short: the raw description carries words nothing else used.
        if len(text) < MOBILE_MIN and record.raw_description:
            tail = clean(record.raw_description)
            if tail and tail not in text:
                candidate = truncate_clean(f"{text}, {tail}", MOBILE_MAX)
                if len(candidate) > len(text):
                    text = candidate

        return truncate_clean(text, MOBILE_MAX) or None

    def _mobile_head(self, record: ProductRecord) -> str:
        """`Whirlpool Corporation` + `Whirlpool(R)` reads as just `Whirlpool`."""
        brand = strip_symbols(record.brand_name or "").strip()
        manufacturer = clean(record.manufacturer_name)
        if not brand:
            return manufacturer
        if not manufacturer:
            return brand
        if brand.casefold() in manufacturer.casefold():
            return brand
        if manufacturer.casefold() in brand.casefold():
            return brand
        return f"{manufacturer} {brand}"

    def _invoice(self, record: ProductRecord, item_type: str) -> str | None:
        """<=40 chars, ALL CAPS, noun first, abbreviated.

        The delivery format writes the item type back to front so the noun
        leads the till receipt: `Industrial Surface Cover` becomes
        `COVER SURF INDL`, `Battery Jump Starter` becomes `STARTER JUMP BAT`.
        """
        noun_first = " ".join(reversed(item_type.split())) if item_type else ""
        segments = [noun_first] + self._body(record, "invoice")
        raw = " ".join(s for s in segments if s)
        if not clean(raw):
            raw = record.raw_description

        text = self.kb.style.abbreviate(strip_symbols(clean(raw)).upper())
        text = _glue_units(text)
        # abbreviate_for_invoice applies the remaining house abbreviations and
        # enforces the hard 40-character ceiling.
        return abbreviate_for_invoice(text, INVOICE_MAX) or None

    def _marketing(self, record: ProductRecord, item_type: str) -> str | None:
        """The one free-prose field, restricted to already-validated attributes.

        When first-party source material was retrieved, its specification pairs
        are handed to the model as well, so the copy can quote facts the
        manufacturer actually published rather than generic filler. The model is
        still told to use nothing that was not given to it.
        """
        facts = [f"{a.label}: {a.rendered()}" for a in record.filled_attributes()]
        if not facts:
            return None

        # Retrieved first-party facts, when any, are the strongest material the
        # copy can carry: they are the manufacturer's own claims for this part.
        source_lines: list[str] = []
        bundle = record.evidence
        if bundle is not None:
            for label, (value, url) in list(bundle.merged_tables().items())[:12]:
                if value and label.casefold() not in {"upc", "gtin"}:
                    source_lines.append(f"{label}: {value}")

        source_block = ""
        if source_lines:
            source_block = (
                "Manufacturer's own published specifications (from its site):\n"
                + "\n".join(f"- {line}" for line in source_lines)
                + "\n"
            )

        prompt = (
            f"Brand: {record.brand_name or 'unknown'}\n"
            f"Product: {item_type}\n"
            f"Series: {record.series or 'none'}\n"
            f"Part number: {record.mpn or 'unknown'}\n"
            f"Verified specifications:\n" + "\n".join(f"- {f}" for f in facts) + "\n"
            f"{source_block}"
            f"Features: {record.features or 'none'}\n\n"
            "Write one marketing paragraph of 2 to 3 sentences for a product "
            "page. Use ONLY the specifications listed above. Do not invent "
            "numbers, materials, certifications or claims. Do not use "
            "superlatives. Write for a trade buyer."
        )
        result = self.llm.generate_json(prompt, MARKETING_SCHEMA, system=(
            "You write factual industrial product copy. You never state a "
            "specification that was not given to you."
        ))
        if not result:
            return None
        return clean(str(result.get("marketing_description", ""))) or None


def _glue_units(text: str) -> str:
    """`120 V` -> `120V`, the compact unit form used on invoice and mobile lines."""
    return _GLUE_UNIT_RE.sub(r"\1\2", text or "")


_BARE_NUMBER_RE = re.compile(r"^[\d.,/\s-]+$")


def _is_bare_number(text: str) -> bool:
    """True for a segment that is only digits and separators, e.g. `2` or `1, 0`."""
    return bool(_BARE_NUMBER_RE.fullmatch(text.strip()))
