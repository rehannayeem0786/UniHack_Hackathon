"""Verify the exported workbook against the organisers' Expected Output sheet.

The brief is explicit: do not change or modify the headers, and populate the
required ones. Those are two separate claims and this checks both.

1. **Header fidelity.** Column count, order and exact strings must match the
   Expected Output sheet. Compared character by character, so a stray space or a
   changed case fails rather than passing quietly.
2. **Population.** Which headers we fill, and — for the ones we do not — whether
   the organisers' own reference rows fill them either. A column blank in the
   reference is not a gap in our output; a column the reference fills and we
   leave empty is, and it is reported as such.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.config import PROJECT_ROOT
from backend.core.schema import delivery_columns
from backend.knowledge.datasets import OUTPUT_FILE, OUTPUT_SHEET

# Columns that cannot be derived from the input and must not be invented.
# Reported separately so the population figure is not quietly flattered.
UNKNOWABLE = {
    "UPC", "EAN", "GTIN", "UNSPSC", "List Price", "Selling Qty", "Selling UOM",
    "Standard Packaging Information", "ALTERNATE_PART_NUMBER", "TRADE_NAME",
    "Discontinued", "Ref URL 1", "Ref URL 2", "Ref URL 3", "Ref URL 4",
    "Ref URL 5",
}


def _filled(series: pd.Series) -> int:
    return int(series.map(lambda v: bool(str(v).strip()) and not pd.isna(v)).sum())


def latest_export(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    candidates = sorted((PROJECT_ROOT / "runs").glob("delivery-*.xlsx"))
    if not candidates:
        raise SystemExit(
            "No export found. Run: python scripts/run_pipeline.py --fold holdout --export"
        )
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", default=None, help="path to a delivery workbook")
    parser.add_argument("--verbose", action="store_true", help="list every column")
    args = parser.parse_args()

    official_path = PROJECT_ROOT / "data" / OUTPUT_FILE
    official = pd.read_excel(official_path, sheet_name=OUTPUT_SHEET, dtype=str)
    expected = [str(c) for c in official.columns]

    export_path = latest_export(args.export)
    ours = pd.read_excel(export_path, dtype=str)
    actual = [str(c) for c in ours.columns]

    print("=" * 74)
    print(" Delivery schema compliance")
    print("=" * 74)
    print(f"  reference : {official_path.name} [{OUTPUT_SHEET}]")
    print(f"  export    : {export_path.name}  ({len(ours)} rows)")
    print()

    # --- 1. header fidelity ------------------------------------------------
    failures: list[str] = []

    print("--- Headers ---")
    print(f"  expected columns          {len(expected)}")
    print(f"  exported columns          {len(actual)}")
    if len(expected) != len(actual):
        failures.append(f"column count differs: {len(actual)} vs {len(expected)}")

    mismatches = [
        (i, want, got)
        for i, (want, got) in enumerate(zip(expected, actual))
        if want != got
    ]
    if mismatches:
        failures.append(f"{len(mismatches)} header(s) differ from the reference")
        for i, want, got in mismatches[:15]:
            print(f"  [{i:3}] expected {want!r}, got {got!r}")
    else:
        print("  order and spelling        identical (character for character)")

    missing = [c for c in expected if c not in actual]
    added = [c for c in actual if c not in expected]
    if missing:
        failures.append(f"{len(missing)} header(s) missing: {missing[:8]}")
    if added:
        failures.append(f"{len(added)} header(s) added: {added[:8]}")
    if not missing and not added:
        print("  no columns added or removed")

    # Also confirm the in-code schema agrees with the workbook, so the exporter
    # cannot drift from the reference without this failing.
    if delivery_columns() != expected:
        failures.append("backend.core.schema.delivery_columns() differs from the sheet")
    else:
        print("  schema module in step      yes")

    # --- 2. population -----------------------------------------------------
    print("\n--- Population ---")
    key = "PART_NUMBER"
    scored = official[official[key].astype(str).isin(ours[key].astype(str))]

    populated: list[str] = []
    blank_in_both: list[str] = []
    gaps: list[tuple[str, int]] = []
    unknowable_blank: list[str] = []

    for column in expected:
        if column not in actual:
            continue
        mine = _filled(ours[column])
        theirs = _filled(scored[column]) if column in scored.columns else 0

        if mine:
            populated.append(column)
        elif theirs == 0:
            blank_in_both.append(column)
        elif column in UNKNOWABLE:
            unknowable_blank.append(column)
        else:
            gaps.append((column, theirs))

    total = len(expected)
    print(f"  populated by us           {len(populated):3} / {total}")
    print(f"  blank in the reference    {len(blank_in_both):3}  (nothing to populate)")
    print(f"  not derivable from input  {len(unknowable_blank):3}  (left blank by design)")
    print(f"  genuine gaps              {len(gaps):3}")

    if gaps:
        print("\n  Columns the reference fills and we do not:")
        for column, theirs in sorted(gaps, key=lambda kv: -kv[1]):
            print(f"    {theirs:4} reference rows   {column}")

    if unknowable_blank:
        print("\n  Left blank rather than fabricated:")
        print("    " + ", ".join(sorted(unknowable_blank)))

    if args.verbose:
        print("\n  Populated columns:")
        for column in populated:
            print(f"    {_filled(ours[column]):4}  {column}")

    # --- verdict -----------------------------------------------------------
    print("\n" + "=" * 74)
    if failures:
        print(" FAIL - headers do not comply")
        for failure in failures:
            print(f"   - {failure}")
        print("=" * 74)
        sys.exit(1)

    print(" PASS - headers match the Expected Output sheet exactly")
    print(f"        {len(populated)} of {total} columns populated")
    print("=" * 74)


if __name__ == "__main__":
    main()
