"""Seed the on-disk LLM and web cache from the bundled dataset.

Running the pipeline once per row writes every LLM response (keyed by a SHA-256
hash of the prompt, tag and schema) and every fetched web source into
`settings.cache_path` (`data/cache/` by default). The cache key deliberately
ignores which model answered, so those seeded responses replay on any later
run - including the deployed service - with identical content and at ~0 cost.

`data/cache/` is committed to the repo (unlike the old hidden `.cache/`), so
the deployment flow is: seed locally with your best model chain, push the
folder to GitHub, and every Render deploy builds with those answers pre-loaded.
No disk, no paid plan, no manual upload.

Examples
--------
    python scripts/seed_cache.py               # holdout fold (the demo rows)
    python scripts/seed_cache.py --fold all    # the full 200-row catalogue
    python scripts/seed_cache.py --limit 10    # a quick smoke seed

Then commit and push the cache:
    git add data/cache && git commit -m "seed cache" && git push
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.knowledge.datasets import load_split, records_from  # noqa: E402
from backend.knowledge.registry import KnowledgeBase  # noqa: E402
from backend.pipeline.orchestrator import EnrichmentPipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-warm the on-disk LLM/web cache so deployed re-runs are "
        "instant and replay locally-tuned answers.",
    )
    parser.add_argument(
        "--fold", choices=("holdout", "train", "all"), default="holdout",
        help="which rows to enrich (default: holdout, the demo rows)",
    )
    parser.add_argument("--limit", type=int, default=0, help="only process N rows")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    split = load_split()

    # Fit the knowledge base on TRAIN only - exactly as the API does - so the
    # seeded responses match what the deployed service would produce. Fitting
    # on the rows being seeded would bake in the test fold and disagree with
    # every live result.
    kb = KnowledgeBase.fit(split.train_truth)
    print(f"Knowledge base fitted on {kb.fitted_rows} training rows")
    print(f"  {json.dumps(kb.summary())}")

    if args.fold == "holdout":
        frame = split.holdout_input
    elif args.fold == "train":
        frame = split.train_input
    else:
        frame = split.inputs

    if args.limit:
        frame = frame.head(args.limit)

    primary = settings.llm_provider.strip().casefold()
    chain = settings.chain_for(primary)
    print(f"\nSeeding {len(frame)} rows from fold={args.fold}")
    print(f"  cache dir : {settings.cache_path}")
    print(f"  chain     : {', '.join(chain)}")
    if not settings.has_api_key:
        print("  WARNING   : no provider API key set - responses will come from "
              "deterministic fallbacks only and the cache will not be warmed.")

    pipeline = EnrichmentPipeline(kb)
    started = time.perf_counter()
    last = [started]

    def progress(done: int, total: int, _record) -> None:
        now = time.perf_counter()
        if now - last[0] > 1.0 or done == total:
            last[0] = now
            eta = (now - started) / (done / total) if done else 0.0
            print(f"    {done}/{total} ({done/total:.0%}) eta ~"
                  f"{eta:.0f}s", end="\r", flush=True)

    result = pipeline.run(records_from(frame), progress=progress)
    print()
    elapsed = time.perf_counter() - started
    usage = result.usage
    print("\n--- Seeded ---")
    print(f"  records       : {len(result.records)} in {elapsed:.1f}s "
          f"({result.summary()['seconds_per_record']}s/row)")
    print(f"  llm live calls: {usage['live_calls']}")
    print(f"  llm cache hits: {usage['cache_hits']}")
    print(f"  llm failures  : {usage['failures']}")
    print(f"  answered by   : {usage['by_model'] or 'none (nothing live)'}")
    print(f"  exhausted     : {usage['exhausted_models'] or 'none'}")
    print(f"  web data  : {result.retrieval}")

    n_cache = 0
    cache_dir = settings.cache_path
    if cache_dir.exists():
        n_cache = sum(1 for _ in cache_dir.glob("*.json"))
    print(f"\nCache now holds {n_cache} JSON entries under {cache_dir}.")
    print("Ready to commit:  git add data/cache data && "
          "git commit -m 'seed cache' && git push")
    print("On Render, CACHE_DIR=data/cache reads these committed answers "
          "directly - no disk or manual upload needed.")
    return 1 if usage["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())