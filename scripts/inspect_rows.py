"""Side-by-side predicted vs ground truth for a handful of rows.

The headline metrics say how well the pipeline does; this says *why*. Run it
after a change to see whether a description is wrong or merely phrased
differently, which the exact-match number cannot distinguish.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.evaluation.scorer import SCORED_FIELDS
from backend.knowledge.datasets import load_split, records_from
from backend.knowledge.registry import KnowledgeBase
from backend.pipeline.orchestrator import EnrichmentPipeline

FIELDS = (
    "Product Name",
    "INVOICE_DESC",
    "MOBILE_DESC",
    "SHORT_DESC",
    "RETAIL_DESC",
    "LONG_DESC1",
)


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--fold", default="holdout", choices=["holdout", "train"])
    parser.add_argument("--holdout-ratio", type=float, default=0.3)
    parser.add_argument("--field", default="", help="show only this field")
    parser.add_argument("--diff", action="store_true", help="show a word-level diff")
    args = parser.parse_args()

    split = load_split(holdout_ratio=args.holdout_ratio)
    kb = KnowledgeBase.fit(split.train_truth)
    inputs = split.holdout_input if args.fold == "holdout" else split.train_input
    truth = split.holdout_truth if args.fold == "holdout" else split.train_truth

    frame = inputs.head(args.limit)
    result = EnrichmentPipeline(kb).run(records_from(frame))
    predicted = result.to_frame()
    truth_indexed = truth.set_index("PART_NUMBER")

    fields = (args.field,) if args.field else FIELDS

    for _, row in predicted.iterrows():
        key = row["PART_NUMBER"]
        if key not in truth_indexed.index:
            continue
        want_row = truth_indexed.loc[key]
        if isinstance(want_row, pd.DataFrame):
            want_row = want_row.iloc[0]

        print("=" * 100)
        print(f"{key}  |  {_text(row.get('Part_Desc'))}")
        print(f"  classpath got  : {_text(row.get('Classpath'))}")
        print(f"  classpath want : {_text(want_row.get('Classpath'))}")
        for field in fields:
            got, want = _text(row.get(field)), _text(want_row.get(field))
            mark = "OK " if got == want else "XX "
            print(f"\n  {mark}{field}")
            print(f"      got  ({len(got):3}): {got}")
            print(f"      want ({len(want):3}): {want}")
            if args.diff and got != want:
                diff = difflib.ndiff(want.split(), got.split())
                changes = [d for d in diff if d[0] in "+-"]
                print(f"      diff      : {' '.join(changes)}")


if __name__ == "__main__":
    main()
