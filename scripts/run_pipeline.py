"""Run the enrichment pipeline and score it against ground truth.

Examples
--------
    python scripts/run_pipeline.py --limit 6            # quick smoke test
    python scripts/run_pipeline.py --fold holdout       # honest evaluation
    python scripts/run_pipeline.py --fold all --export  # full run + Excel
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from backend.config import PROJECT_ROOT  # noqa: E402
from backend.evaluation.scorer import Evaluator  # noqa: E402
from backend.knowledge.datasets import load_split  # noqa: E402
from backend.knowledge.registry import KnowledgeBase  # noqa: E402
from backend.pipeline.orchestrator import EnrichmentPipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the enrichment pipeline")
    parser.add_argument(
        "--fold", choices=("holdout", "train", "all"), default="holdout",
        help="which rows to enrich (default: holdout, the honest measurement)",
    )
    parser.add_argument("--limit", type=int, default=0, help="only process N rows")
    parser.add_argument("--holdout-ratio", type=float, default=0.3)
    parser.add_argument("--export", action="store_true", help="write an Excel delivery file")
    parser.add_argument("--verbose", action="store_true", help="print each enriched row")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    split = load_split(holdout_ratio=args.holdout_ratio)

    # The knowledge base is always fitted on TRAIN only. Scoring the holdout
    # fold therefore measures generalisation, not memorisation.
    kb = KnowledgeBase.fit(split.train_truth)
    print(f"Knowledge base fitted on {kb.fitted_rows} training rows")
    print(f"  {json.dumps(kb.summary())}")

    if args.fold == "holdout":
        inputs, truth = split.holdout_input, split.holdout_truth
    elif args.fold == "train":
        inputs, truth = split.train_input, split.train_truth
    else:
        inputs = pd.concat([split.train_input, split.holdout_input], ignore_index=True)
        truth = pd.concat([split.train_truth, split.holdout_truth], ignore_index=True)

    if args.limit:
        inputs = inputs.head(args.limit)
        truth = truth[truth.PART_NUMBER.isin(inputs.PART_NUMBER)]

    print(f"\nEnriching {len(inputs)} rows from fold={args.fold} ...")
    if args.fold != "holdout":
        print("  NOTE: this fold overlaps the KB training data; scores are optimistic.")

    pipeline = EnrichmentPipeline(kb)
    if not pipeline.llm.available:
        print("  WARNING: no GEMINI_API_KEY, running on deterministic fallbacks only.")

    last = [0.0]

    def progress(done: int, total: int, _record) -> None:
        now = time.perf_counter()
        if now - last[0] > 1.0 or done == total:
            last[0] = now
            print(f"    {done}/{total} ({done/total:.0%})", end="\r", flush=True)

    result = pipeline.run(
        __import__("backend.knowledge.datasets", fromlist=["records_from"]).records_from(inputs),
        progress=progress,
    )
    print()

    print("\n--- Pipeline ---")
    for key, value in result.summary().items():
        print(f"  {key:22s} {value}")

    predicted = result.to_frame()
    metrics = Evaluator().evaluate(predicted, truth, result.records)

    print("\n--- Field accuracy (vs ground truth) ---")
    print(f"  {'FIELD':<28}{'CMP':>5}{'EXACT':>8}{'FUZZY':>8}{'SIM':>7}{'FILL':>7}")
    for row in metrics["fields"]:
        print(
            f"  {row['field']:<28}{row['compared']:>5}"
            f"{row['exact_match']:>8.0%}{row['fuzzy_match']:>8.0%}"
            f"{row['mean_similarity']:>7.0%}{row['fill_rate']:>7.0%}"
        )
    head = metrics["headline"]
    print(f"\n  mean exact match : {head['mean_exact_match']:.1%}")
    print(f"  mean fuzzy match : {head['mean_fuzzy_match']:.1%}")

    print("\n--- Attributes ---")
    for key, value in metrics["attributes"].items():
        print(f"  {key:22s} {value}")

    print("\n--- Rule compliance ---")
    for key, value in metrics["compliance"].items():
        print(f"  {key:28s} {value:.1%}")

    print("\n--- Coverage ---")
    for key, value in metrics["coverage"].items():
        print(f"  {key:22s} {value}")

    if "sourcing" in metrics:
        print("\n--- Traceability (first-party retrieval) ---")
        for key, value in metrics["sourcing"].items():
            print(f"  {key:28s} {value}")

    if args.verbose:
        for record in result.records:
            print(f"\n=== {record.part_number} | {record.raw_description}")
            print(f"  classpath : {record.classpath}")
            print(f"  brand     : {record.brand_name}  ({record.manufacturer_name})")
            print(f"  invoice   : {record.invoice_desc}")
            print(f"  mobile    : {record.mobile_desc}")
            print(f"  short     : {record.short_desc}")
            print(f"  long      : {record.long_desc}")
            print(f"  conf      : {record.confidence.get('overall')}  review={record.needs_review}")
            for issue in record.issues:
                print(f"    ! {issue}")

    runs = PROJECT_ROOT / "runs"
    runs.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (runs / f"metrics-{args.fold}-{stamp}.json").write_text(
        json.dumps({"pipeline": result.summary(), "metrics": metrics}, indent=2),
        encoding="utf-8",
    )
    print(f"\nMetrics -> runs/metrics-{args.fold}-{stamp}.json")

    if args.export:
        path = runs / f"delivery-{args.fold}-{stamp}.xlsx"
        predicted.to_excel(path, index=False, sheet_name="Delivery Format")
        print(f"Delivery -> {path.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
