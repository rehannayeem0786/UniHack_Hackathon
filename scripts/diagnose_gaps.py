"""Diagnose where the latest holdout run loses points vs ground truth.

Reads the most recent delivery workbook in runs/ and the holdout truth, then
reports: per-column fill gaps, and the actual mismatched values for identity
fields. Output drives the next round of improvements.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backend.knowledge.datasets import load_split

RUNS = Path(__file__).resolve().parent.parent / "runs"


def main() -> int:
    deliveries = sorted(RUNS.glob("delivery-holdout-*.xlsx"))
    if not deliveries:
        print("no delivery workbook found")
        return 1
    latest = deliveries[-1]
    print(f"Predicted: {latest.name}")

    pred = pd.read_excel(latest, dtype=str)
    split = load_split()
    truth = split.holdout_truth

    pred = pred.set_index("PART_NUMBER") if "PART_NUMBER" in pred.columns else pred
    truth = truth.set_index("PART_NUMBER")
    common = pred.index.intersection(truth.index)
    pred, truth = pred.loc[common], truth.loc[common]
    print(f"rows compared: {len(common)}\n")

    def filled(frame: pd.DataFrame, col: str) -> int:
        if col not in frame.columns:
            return -1
        series = frame[col]
        return int((series.notna() & (series.astype(str).str.strip() != "")).sum())

    # --- coverage gaps: columns truth fills but we leave blank ---
    print("=== COVERAGE GAPS (truth fills, we don't) ===")
    gaps = []
    for col in truth.columns:
        t = filled(truth, col)
        p = filled(pred, col)
        if p == -1:
            gaps.append((col, t, p, "MISSING COLUMN"))
        elif t > p:
            gaps.append((col, t, p, ""))
    gaps.sort(key=lambda g: g[1] - max(g[2], 0), reverse=True)
    for col, t, p, note in gaps[:40]:
        print(f"  {col:<38} truth={t:>3} ours={p:>3}  {note}")

    # --- identity mismatches ---
    for col in ("MANUFACTURER_NAME", "BRAND_NAME", "Classpath", "Product Name"):
        if col not in pred.columns or col not in truth.columns:
            continue
        print(f"\n=== {col} mismatches ===")
        n = 0
        for part in common:
            p = str(pred.at[part, col] or "").strip()
            t = str(truth.at[part, col] or "").strip()
            if p != t:
                n += 1
                if n <= 12:
                    print(f"  {part}: ours={p!r} truth={t!r}")
        print(f"  total mismatches: {n}/{len(common)}")

    # --- attribute value mismatches (first 10 attribute slots) ---
    print("\n=== ATTRIBUTE VALUE mismatches (sampled) ===")
    n_mismatch = n_compared = 0
    examples = []
    for i in range(1, 51):
        lbl, val = f"ATTRIBUTE_LABEL {i}", f"ATTRIBUTE_VALUE {i}"
        if lbl not in truth.columns:
            break
        for part in common:
            t_label = str(truth.at[part, lbl] or "").strip()
            t_val = str(truth.at[part, val] or "").strip() if val in truth.columns else ""
            if not t_label or not t_val:
                continue
            # find same label in our row
            p_val = ""
            for j in range(1, 51):
                if str(pred.at[part, f"ATTRIBUTE_LABEL {j}"] or "").strip() == t_label:
                    p_val = str(pred.at[part, f"ATTRIBUTE_VALUE {j}"] or "").strip()
                    break
            n_compared += 1
            if p_val != t_val:
                n_mismatch += 1
                if len(examples) < 20:
                    examples.append((part, t_label, p_val, t_val))
    for part, label, p, t in examples:
        print(f"  {part} {label}: ours={p!r} truth={t!r}")
    print(f"  attribute value mismatches: {n_mismatch}/{n_compared}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
