"""Dataset loading and the train / holdout split used for honest evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backend.config import settings
from backend.core.normalize import clean, repair_symbols, strip_condition
from backend.core.schema import INPUT_COLUMNS, ProductRecord

INPUT_FILE = "Unilog_Input_200_Items.xlsx"
OUTPUT_FILE = "Unilog_Output_Delivery_Format.xlsx"
INPUT_SHEET = "Input - 200 Items"
OUTPUT_SHEET = "Delivery Format - 200 Items"


def _read(path: Path, sheet: str) -> pd.DataFrame:
    frame = pd.read_excel(path, sheet_name=sheet, dtype=str)
    # Excel keeps ® / ™ intact; the CSV copies do not, so we always read Excel.
    return frame.map(lambda v: repair_symbols(v) if isinstance(v, str) else v)


def load_inputs(data_dir: Path | None = None) -> pd.DataFrame:
    directory = data_dir or settings.data_dir
    return _read(directory / INPUT_FILE, INPUT_SHEET)


def load_ground_truth(data_dir: Path | None = None) -> pd.DataFrame:
    directory = data_dir or settings.data_dir
    return _read(directory / OUTPUT_FILE, OUTPUT_SHEET)


# --- split ------------------------------------------------------------------


def fold_of(part_number: str, holdout_ratio: float = 0.3) -> str:
    """Assign a row to `train` or `holdout` by hashing its part number.

    Hashing rather than random sampling keeps the split identical across runs
    and machines without needing to store a seed or an index file.
    """
    digest = hashlib.sha256(str(part_number).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 1000
    return "holdout" if bucket < holdout_ratio * 1000 else "train"


@dataclass
class SplitData:
    train_input: pd.DataFrame
    train_truth: pd.DataFrame
    holdout_input: pd.DataFrame
    holdout_truth: pd.DataFrame
    holdout_ratio: float = 0.3

    @property
    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train_input), "holdout": len(self.holdout_input)}

    @property
    def inputs(self) -> pd.DataFrame:
        """Both folds recombined, for enriching the whole dataset."""
        return pd.concat([self.train_input, self.holdout_input], ignore_index=True)

    @property
    def truth(self) -> pd.DataFrame:
        return pd.concat([self.train_truth, self.holdout_truth], ignore_index=True)


def load_split(
    holdout_ratio: float = 0.3, data_dir: Path | None = None
) -> SplitData:
    """Load inputs and ground truth, aligned and split into two folds."""
    inputs = load_inputs(data_dir)
    truth = load_ground_truth(data_dir)

    inputs["_fold"] = inputs["PART_NUMBER"].map(lambda p: fold_of(p, holdout_ratio))
    truth["_fold"] = truth["PART_NUMBER"].map(lambda p: fold_of(p, holdout_ratio))

    return SplitData(
        train_input=inputs[inputs._fold == "train"].drop(columns="_fold").reset_index(drop=True),
        train_truth=truth[truth._fold == "train"].drop(columns="_fold").reset_index(drop=True),
        holdout_input=inputs[inputs._fold == "holdout"].drop(columns="_fold").reset_index(drop=True),
        holdout_truth=truth[truth._fold == "holdout"].drop(columns="_fold").reset_index(drop=True),
        holdout_ratio=holdout_ratio,
    )


# --- row -> record ----------------------------------------------------------


def to_record(row: pd.Series) -> ProductRecord:
    """Build a `ProductRecord` from a raw input row, dropping placeholders."""
    def get(key: str) -> str:
        return clean(row.get(key))

    def raw(key: str) -> str:
        """Untouched cell value — placeholders and all — for the echo columns."""
        value = row.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return repair_symbols(str(value)).strip()

    hints = [get("DIB_Brand"), get("Unilog_Brand"), get("E1_Brand")]
    return ProductRecord(
        part_number=get("PART_NUMBER"),
        sku=get("SKU - MY_PART_NUMBER"),
        dept=get("Dept"),
        **{"class": get("Class")},
        fine=get("Fine"),
        # The verbatim description (condition suffix and all) is preserved in
        # `source_row` for the echo columns; the working copy drops the listing
        # condition so no enriched field can inherit "Display Only" and such.
        raw_description=strip_condition(get("Part_Desc")),
        raw_mpn=get("Mfg_Part_Num"),
        raw_manufacturer=get("Part_Manuf"),
        brand_hints=[h for h in hints if h],
        source_row={column: raw(column) for column in INPUT_COLUMNS},
    )


def records_from(frame: pd.DataFrame) -> list[ProductRecord]:
    return [to_record(row) for _, row in frame.iterrows()]
