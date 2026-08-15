"""Audit brand and manufacturer resolution on the holdout fold.

Resolution feeds every downstream field: a wrong brand corrupts four
descriptions and every asset filename. This isolates the stage so its accuracy
and the provenance of each decision can be read directly, without the LLM
stages in the way.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from backend.knowledge.datasets import load_split, to_record
from backend.knowledge.registry import KnowledgeBase


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-ratio", type=float, default=0.3)
    parser.add_argument("--show", default="wrong", choices=["wrong", "all", "right"])
    args = parser.parse_args()

    split = load_split(holdout_ratio=args.holdout_ratio)
    kb = KnowledgeBase.fit(split.train_truth)
    truth = split.holdout_truth.set_index("PART_NUMBER")

    print(f"learned mpn prefixes : {len(kb.manufacturers.mpn_prefix_brand)}")
    print(f"learned desc tokens  : {len(kb.manufacturers.desc_token_brand)}")
    print(f"known brands         : {len(kb.manufacturers.brand_forms)}\n")

    by_provenance: dict[str, Counter] = {}
    rows = []

    for _, raw in split.holdout_input.iterrows():
        record = to_record(raw)
        key = record.part_number
        if key not in truth.index:
            continue
        want_row = truth.loc[key]
        if isinstance(want_row, pd.DataFrame):
            want_row = want_row.iloc[0]

        manufacturer, brand, confidence, provenance = kb.manufacturers.resolve(
            record.raw_manufacturer,
            record.brand_hints,
            mpn=record.raw_mpn,
            description=record.raw_description,
        )
        want_brand = _text(want_row.get("BRAND_NAME"))
        correct = brand == want_brand

        family = provenance.split(":")[0]
        by_provenance.setdefault(family, Counter())["total"] += 1
        by_provenance[family]["hit" if correct else "miss"] += 1
        rows.append((correct, key, record.raw_description, brand, want_brand, provenance, confidence))

    total = len(rows)
    hits = sum(1 for r in rows if r[0])
    print(f"registry-only brand accuracy: {hits}/{total} = {hits/(total or 1):.1%}\n")

    print(f"{'PROVENANCE':22} {'N':>4} {'HIT':>4} {'ACC':>7}")
    for family, counter in sorted(by_provenance.items(), key=lambda kv: -kv[1]["total"]):
        n, hit = counter["total"], counter["hit"]
        print(f"{family:22} {n:4} {hit:4} {hit/(n or 1):6.1%}")

    print()
    for correct, key, desc, got, want, provenance, confidence in rows:
        if args.show == "wrong" and correct:
            continue
        if args.show == "right" and not correct:
            continue
        mark = "OK" if correct else "XX"
        print(f"{mark} {key}  {desc[:44]:46}")
        print(f"     got={got!r:28} want={want!r:28} via={provenance} @{confidence:.2f}")


if __name__ == "__main__":
    main()
