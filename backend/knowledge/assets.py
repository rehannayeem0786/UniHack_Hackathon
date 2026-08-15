"""Digital assets, sourcing URLs and the packaging columns.

Three different kinds of value live at the tail of the delivery format, and
they need three different levels of caution.

**Naming conventions are safe to generate.** Asset filenames are not scraped,
they are constructed: every row in the labelled set follows
`{brand_prefix}_{part_number}.jpg`, with `_1`..`_4` for alternates and a
document label for PDFs. That is a deterministic rule, so applying it is
reproducing a standard rather than inventing a fact. It is still gated on
evidence: a specification sheet is only named for a brand that is known to
publish specification sheets, because asserting a document exists when it does
not is exactly the fabrication the brief warns about.

**Sourcing URLs are constrained, not invented.** The content guidelines require
product data to come from the manufacturer's own site and explicitly exclude
marketplaces and distributor sites. So the registry learns brand -> approved
manufacturer domain from the labelled rows and emits that domain. It does not
guess a deep product path it has not seen; a verified domain is a true
statement, a fabricated URL is not.

**Per-brand facts are copied, never guessed.** Warranty, country of origin and
the Prop 65 notice are recorded per brand and only reused where the brand
agrees, since they are legal text.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from backend.core.normalize import clean, normalize_measure_text, repair_symbols, strip_symbols

# Document columns we are willing to name, and the filename label each uses.
DOCUMENT_COLUMNS: tuple[str, ...] = (
    "Specification Sheet",
    "Instruction/Installation Manual",
    "Catalog",
    "Owners/User Manual",
    "Warranty Information",
    "Submittal",
    "Service Manual",
    "Line Drawing",
)

IMAGE_COLUMN = "Product Image"
ALTERNATE_COLUMNS: tuple[str, ...] = tuple(f"Alternate Image {i}" for i in range(1, 5))

# Brand-level facts that are copied verbatim when the brand matches.
_FACT_COLUMNS: tuple[str, ...] = ("Warranty", "Country Of Origin", "Prop 65")

# `33-7/16 in H x 23-7/8 in W x 22-5/8 in D` -> one axis per match.
_AXIS_RE = re.compile(
    r"([\d./-]+)\s*(in|ft|mm|cm|m)?\s*(H|W|D|L)\b", re.IGNORECASE
)

_AXIS_TO_COLUMN = {"h": "HEIGHT", "w": "WIDTH", "d": "LENGTH", "l": "LENGTH"}


def _cell(row: pd.Series, key: str) -> str:
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean(repair_symbols(str(value)))


def _brand_key(brand: str) -> str:
    """Loose key so `Speed Queen(R)` and `speed queen` collapse together."""
    return re.sub(r"[^a-z0-9]+", " ", strip_symbols(brand or "").casefold()).strip()


def slugify_asset(text: str) -> str:
    """`Speed Queen(R)` -> `Speed_Queen`, `FS C01 2004S` -> `FS_C01_2004S`."""
    cleaned = strip_symbols(clean(text))
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", cleaned)
    return cleaned.strip("_")


def document_label(column: str) -> str:
    """`Instruction/Installation Manual` -> `Instruction_Installation_Manual`."""
    return re.sub(r"[^A-Za-z0-9]+", "_", column).strip("_")


@dataclass
class AssetRegistry:
    """Per-brand sourcing domains, asset naming prefixes and legal text."""

    brand_domain: dict[str, str] = field(default_factory=dict)
    # brand key -> the prefix its asset filenames actually use. Learned rather
    # than slugified, because `Profile(TM)` files are named `GE_Appliances_*`.
    brand_prefix: dict[str, str] = field(default_factory=dict)
    # brand key -> column -> share of rows where that document was supplied
    brand_documents: dict[str, dict[str, float]] = field(default_factory=dict)
    # brand key -> column -> verbatim legal text
    brand_facts: dict[str, dict[str, str]] = field(default_factory=dict)
    # brand key -> share of rows carrying a product image at all
    brand_image_rate: dict[str, float] = field(default_factory=dict)
    actual_image_value: str = "Yes"

    # -- lookups ------------------------------------------------------------
    def domain_for(self, brand: str, manufacturer: str = "") -> str:
        for candidate in (brand, manufacturer):
            key = _brand_key(candidate)
            if key and key in self.brand_domain:
                return self.brand_domain[key]
        return ""

    def prefix_for(self, brand: str, manufacturer: str = "") -> str:
        for candidate in (brand, manufacturer):
            key = _brand_key(candidate)
            if key and key in self.brand_prefix:
                return self.brand_prefix[key]
        return slugify_asset(brand or manufacturer)

    def publishes(self, brand: str, column: str, threshold: float = 0.5) -> bool:
        """True when this brand is known to supply that document type."""
        key = _brand_key(brand)
        return self.brand_documents.get(key, {}).get(column, 0.0) >= threshold

    def image_rate(self, brand: str) -> float:
        return self.brand_image_rate.get(_brand_key(brand), 0.0)

    def fact_for(self, brand: str, column: str) -> str:
        return self.brand_facts.get(_brand_key(brand), {}).get(column, "")

    # -- fitting ------------------------------------------------------------
    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "AssetRegistry":
        domains: dict[str, Counter] = defaultdict(Counter)
        prefixes: dict[str, Counter] = defaultdict(Counter)
        documents: dict[str, Counter] = defaultdict(Counter)
        facts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        rows_per_brand: Counter = Counter()
        images: Counter = Counter()
        actual: Counter = Counter()

        for _, row in frame.iterrows():
            brand = _cell(row, "BRAND_NAME")
            manufacturer = _cell(row, "MANUFACTURER_NAME")
            key = _brand_key(brand) or _brand_key(manufacturer)
            if not key:
                continue
            rows_per_brand[key] += 1

            url = _cell(row, "MFR URL")
            if url:
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    domains[key][f"{parsed.scheme}://{parsed.netloc}"] += 1

            image = _cell(row, IMAGE_COLUMN)
            if image:
                images[key] += 1
                prefix = _prefix_from_filename(image, _cell(row, "MANUFACTURER_PART_NUMBER"))
                if prefix:
                    prefixes[key][prefix] += 1

            # Alternate images are tracked alongside documents: both answer the
            # same question, namely whether this brand supplies that asset at all.
            for column in (*DOCUMENT_COLUMNS, *ALTERNATE_COLUMNS):
                if _cell(row, column):
                    documents[key][column] += 1

            for column in _FACT_COLUMNS:
                value = _cell(row, column)
                if value:
                    facts[key][column][value] += 1

            flag = _cell(row, "Actual Image (Yes/No)")
            if flag:
                actual[flag] += 1

        return cls(
            brand_domain={k: c.most_common(1)[0][0] for k, c in domains.items() if c},
            brand_prefix={k: c.most_common(1)[0][0] for k, c in prefixes.items() if c},
            brand_documents={
                k: {
                    column: round(hits / (rows_per_brand[k] or 1), 3)
                    for column, hits in counter.items()
                }
                for k, counter in documents.items()
            },
            brand_facts={
                k: {column: c.most_common(1)[0][0] for column, c in columns.items() if c}
                for k, columns in facts.items()
            },
            brand_image_rate={
                k: round(hits / (rows_per_brand[k] or 1), 3) for k, hits in images.items()
            },
            actual_image_value=actual.most_common(1)[0][0] if actual else "Yes",
        )

    # -- reporting ----------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        return {
            "brand_domain": dict(sorted(self.brand_domain.items())),
            "brand_prefix": dict(sorted(self.brand_prefix.items())),
            "brand_documents": {k: v for k, v in sorted(self.brand_documents.items())},
        }

    def summary(self) -> dict[str, int]:
        return {
            "sourcing_domains": len(self.brand_domain),
            "asset_prefixes": len(self.brand_prefix),
        }


def _prefix_from_filename(filename: str, mpn: str) -> str:
    """Recover the brand prefix from `Speed_Queen_DR7004BE.jpg`."""
    stem = filename.rsplit(".", 1)[0]
    marker = slugify_asset(mpn)
    if marker and marker in stem:
        return stem.split(marker)[0].strip("_")
    # No part number in the name: fall back to everything but the last token.
    pieces = stem.split("_")
    return "_".join(pieces[:-1]) if len(pieces) > 1 else ""


def dimensions_from(text: str) -> dict[str, str]:
    """Split a size string into the LENGTH / WIDTH / HEIGHT delivery columns.

    `33-7/16 in H x 23-7/8 in W x 22-5/8 in D` yields HEIGHT, WIDTH and LENGTH
    with their units. Returns an empty mapping when the string is not an
    axis-labelled dimension, so a bare `24 in` is left alone rather than
    guessed at.
    """
    out: dict[str, str] = {}
    source = normalize_measure_text(text or "")
    for value, unit, axis in _AXIS_RE.findall(source):
        column = _AXIS_TO_COLUMN.get(axis.casefold())
        if not column or column in out:
            continue
        out[column] = value.strip()
        out[f"{column}_UOM"] = (unit or "in").strip()
    return out
