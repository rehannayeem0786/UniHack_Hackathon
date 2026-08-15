"""The 252-column delivery schema, plus typed records used across the pipeline.

Column names here must match the Delivery Format sheet exactly; the exporter
relies on them to rebuild the sheet in the original order.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# --- Placeholders that mean "this field is empty" ---------------------------
PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "-- unbranded --",
        "-- no unilog brand --",
        "-- no dib brand --",
        "-- no brand --",
        "-",
        "--",
        "n/a",
        "na",
        "none",
        "null",
        "",
    }
)

# --- Input columns ----------------------------------------------------------
INPUT_COLUMNS: tuple[str, ...] = (
    "PART_NUMBER",
    "Dept",
    "Class",
    "Fine",
    "SKU - MY_PART_NUMBER",
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
)

# --- Enriched core fields we generate --------------------------------------
CORE_FIELDS: tuple[str, ...] = (
    "MANUFACTURER_NAME",
    "BRAND_NAME",
    "MANUFACTURER_PART_NUMBER",
    "Classpath",
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
    "MARKETING_DESCRIPTION",
)

META_FIELDS: tuple[str, ...] = (
    "With",
    "Standard/Approvals",
    "Prop 65",
    "Application",
    "Includes",
    "Product Name",
)

MAX_ATTRIBUTES = 50
MAX_FEATURES = 20

# --- Hard character limits from the Column Guide ---------------------------
CHAR_LIMITS: dict[str, tuple[int, int]] = {
    # field: (min_len, max_len)
    "INVOICE_DESC": (0, 40),
    "MOBILE_DESC": (60, 80),
}

ALL_CAPS_FIELDS: frozenset[str] = frozenset({"INVOICE_DESC"})


def _fallback_columns() -> list[str]:
    """Reconstruct the delivery header when the reference workbook is absent."""
    cols: list[str] = ["MFR URL"]
    cols += [f"Ref URL {i}" for i in range(1, 6)]
    cols += list(INPUT_COLUMNS)
    cols += [
        "MANUFACTURER_NAME", "BRAND_NAME", "TRADE_NAME",
        "MANUFACTURER_PART_NUMBER", "ALTERNATE_PART_NUMBER", "Classpath",
        "MOBILE_DESC", "INVOICE_DESC", "SHORT_DESC", "LONG_DESC1",
        "RETAIL_DESC", "MARKETING_DESCRIPTION",
    ]
    cols += [f"ITEM_FEATURES_{i}" for i in range(1, MAX_FEATURES + 1)]
    cols += list(META_FIELDS)
    for i in range(1, MAX_ATTRIBUTES + 1):
        cols += [f"ATTRIBUTE_LABEL {i}", f"ATTRIBUTE_VALUE {i}", f"ATTRIBUTE_UOM {i}"]
    cols += [
        "UPC", "EAN", "GTIN", "UNSPSC", "Warranty", "List Price",
        "Selling Qty", "Selling UOM", "Standard Packaging Information",
        "LENGTH", "LENGTH_UOM", "HEIGHT", "HEIGHT_UOM", "WIDTH", "WIDTH_UOM",
        "WEIGHT", "WEIGHT_UOM", "VOLUME", "VOLUME_UOM",
        "Product Image", "Alternate Image 1", "Alternate Image 2",
        "Alternate Image 3", "Alternate Image 4", "SDS", "SDS_1",
        "Warranty Information", "Catalog", "Specification Sheet",
        "Instruction/Installation Manual", "Service Manual",
        "Owners/User Manual", "Line Drawing", "MTR", "RoHS",
        "Full Engineering Drawing", "Energy Star Guide", "Technical Bulletin",
        "Submittal", "Compatibility Chart", "Size Chart",
        "Product Label/Insert", "Video Link", "Video Link 1",
        "Country Of Origin", "Discontinued", "Actual Image (Yes/No)",
    ]
    return cols


@lru_cache(maxsize=1)
def delivery_columns() -> list[str]:
    """The 252-column delivery header, in the order the organisers specify.

    Read from the official Expected Output workbook so the exported sheet is
    byte-for-byte column compatible. Hardcoding this order previously dropped
    TRADE_NAME and ALTERNATE_PART_NUMBER and swapped RETAIL_DESC with
    MARKETING_DESCRIPTION, so the file is treated as the source of truth and
    the hardcoded list is only a fallback.
    """
    reference = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "Unilog_Output_Delivery_Format.xlsx"
    )
    if reference.exists():
        try:
            import pandas as pd

            header = pd.read_excel(
                reference, sheet_name="Delivery Format - 200 Items", nrows=0
            )
            columns = [str(c) for c in header.columns]
            if len(columns) >= 250:
                return columns
        except Exception:  # noqa: BLE001 - fall back rather than fail at import
            pass
    return _fallback_columns()


class Attribute(BaseModel):
    """One attribute triplet: label, value, and optional unit of measure."""

    label: str
    value: str | None = None
    uom: str | None = None
    confidence: float = 1.0
    source: str = "derived"

    @property
    def is_filled(self) -> bool:
        return bool(self.value and str(self.value).strip())

    def rendered(self) -> str:
        """Value with its unit, e.g. `24 in`. Empty string when unfilled."""
        if not self.is_filled:
            return ""
        value = str(self.value).strip()
        return f"{value} {self.uom}".strip() if self.uom else value


class ProductRecord(BaseModel):
    """A single enriched product, carried through every pipeline stage."""

    # identity / passthrough
    part_number: str = ""
    sku: str = ""
    dept: str = ""
    klass: str = Field(default="", alias="class")
    fine: str = ""
    raw_description: str = ""
    raw_mpn: str = ""
    raw_manufacturer: str = ""
    brand_hints: list[str] = Field(default_factory=list)
    # The input row exactly as supplied, keyed by input column name. The delivery
    # format echoes all eleven input columns verbatim — placeholders included —
    # so they are echoed from here rather than reassembled field by field, which
    # is how E1_Brand, Unilog_Brand and DIB_Brand were previously dropped.
    source_row: dict[str, str] = Field(default_factory=dict)

    # enriched
    manufacturer_name: str | None = None
    brand_name: str | None = None
    mpn: str | None = None
    classpath: str | None = None
    product_name: str | None = None
    series: str | None = None
    with_clause: str | None = None

    attributes: list[Attribute] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    includes: str | None = None

    invoice_desc: str | None = None
    mobile_desc: str | None = None
    short_desc: str | None = None
    long_desc: str | None = None
    retail_desc: str | None = None
    marketing_desc: str | None = None

    mfr_url: str | None = None
    # Tail-of-schema columns (assets, documents, dimensions, legal text) keyed
    # by their exact delivery column name. A bag rather than thirty fields,
    # because they are all set by one agent and never read by another.
    extras: dict[str, str] = Field(default_factory=dict)

    # observability
    confidence: dict[str, float] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    needs_review: bool = False

    # --- retrieved manufacturer sources ---
    # The live `EvidenceBundle`, excluded from serialisation because it carries
    # whole documents. Typed loosely to keep `core` free of a dependency on
    # `sourcing`; the research stage is the only writer.
    evidence: Any = Field(default=None, exclude=True, repr=False)
    # The serialisable half: one entry per document actually read, so the API
    # and the UI can show where a value came from without shipping the text.
    citations: list[dict[str, Any]] = Field(default_factory=list)
    # Attribute label -> the source URL the value was verified against.
    grounded: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @property
    def grounded_count(self) -> int:
        """Filled attributes whose value appears verbatim in a retrieved source."""
        return len(self.grounded)

    def attribute(self, label: str) -> Attribute | None:
        target = label.strip().casefold()
        for attr in self.attributes:
            if attr.label.strip().casefold() == target:
                return attr
        return None

    def attribute_value(self, label: str) -> str:
        attr = self.attribute(label)
        return attr.rendered() if attr else ""

    def filled_attributes(self) -> list[Attribute]:
        return [a for a in self.attributes if a.is_filled]

    def to_delivery_row(self) -> dict[str, Any]:
        """Flatten into a single 252-column delivery row."""
        row: dict[str, Any] = {c: None for c in delivery_columns()}
        row.update(
            {
                "MFR URL": self.mfr_url,
                "MANUFACTURER_NAME": self.manufacturer_name,
                "BRAND_NAME": self.brand_name,
                "MANUFACTURER_PART_NUMBER": self.mpn,
                "Classpath": self.classpath,
                "Product Name": self.product_name,
                "With": self.with_clause,
                "Includes": self.includes,
                "MOBILE_DESC": self.mobile_desc,
                "INVOICE_DESC": self.invoice_desc,
                "SHORT_DESC": self.short_desc,
                "LONG_DESC1": self.long_desc,
                "RETAIL_DESC": self.retail_desc,
                "MARKETING_DESCRIPTION": self.marketing_desc,
                "Standard/Approvals": "|".join(self.approvals) or None,
            }
        )

        # Every input column, echoed exactly as received, and echoed last so the
        # source always wins. Placeholders such as "-- Unbranded --" are kept on
        # purpose: the reference sheet keeps them, and these columns are the
        # record of what the distributor actually supplied. Cleaned fields are
        # only a fallback, for records not built from an input row.
        fallbacks: dict[str, str] = {
            "PART_NUMBER": self.part_number,
            "Dept": self.dept,
            "Class": self.klass,
            "Fine": self.fine,
            "SKU - MY_PART_NUMBER": self.sku,
            "Mfg_Part_Num": self.raw_mpn,
            "Part_Desc": self.raw_description,
            "Part_Manuf": self.raw_manufacturer,
        }
        for column in INPUT_COLUMNS:
            value = self.source_row.get(column) or fallbacks.get(column, "")
            row[column] = str(value) if value and str(value).strip() else None

        for i, feature in enumerate(self.features[:MAX_FEATURES], start=1):
            row[f"ITEM_FEATURES_{i}"] = feature
        for i, attr in enumerate(self.attributes[:MAX_ATTRIBUTES], start=1):
            row[f"ATTRIBUTE_LABEL {i}"] = attr.label
            row[f"ATTRIBUTE_VALUE {i}"] = attr.value or None
            row[f"ATTRIBUTE_UOM {i}"] = attr.uom or None
        # Tail columns last, and only ones the schema actually defines, so a
        # typo in an agent cannot widen the delivery sheet.
        for column, value in self.extras.items():
            if column in row and value:
                row[column] = value
        return row
