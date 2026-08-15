"""Fit the knowledge base on the training fold and report what it learned.

Usage:  python scripts/build_kb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import PROJECT_ROOT  # noqa: E402
from backend.knowledge.datasets import load_split  # noqa: E402
from backend.knowledge.registry import KnowledgeBase  # noqa: E402


def main() -> int:
    split = load_split(holdout_ratio=0.3)
    print(f"Split: {split.sizes}")

    kb = KnowledgeBase.fit(split.train_truth)
    print("\n--- Knowledge base (fitted on TRAIN only) ---")
    for key, value in kb.summary().items():
        print(f"  {key:28s} {value}")

    out = PROJECT_ROOT / "artifacts" / "knowledge_base.json"
    kb.save(out)
    print(f"\nSaved -> {out.relative_to(PROJECT_ROOT)} ({out.stat().st_size/1024:.0f} KB)")

    print("\n--- Sample attribute templates ---")
    for cp in kb.taxonomy.all_classpaths[:4]:
        print(f"  {cp.split('>')[-1]}")
        print(f"    {' | '.join(kb.taxonomy.template_for(cp))}")

    print("\n--- Sample controlled vocabularies ---")
    shown = 0
    for (cp, label), counter in kb.attributes.values.items():
        if len(counter) >= 3 and shown < 6:
            print(f"  [{cp.split('>')[-1]}] {label}: {list(counter)[:6]}")
            shown += 1

    # Generalisation probe: can the KB cover holdout rows it never saw?
    print("\n--- Holdout coverage (generalisation check) ---")
    covered = template = 0
    for _, row in split.holdout_input.iterrows():
        cands = kb.taxonomy.candidates(
            str(row.get("Dept") or ""), str(row.get("Class") or ""), str(row.get("Fine") or "")
        )
        if cands:
            covered += 1
            if kb.taxonomy.template_for(cands[0]):
                template += 1
    total = len(split.holdout_input)
    print(f"  classpath candidates found : {covered}/{total} ({covered/total:.0%})")
    print(f"  attribute template found   : {template}/{total} ({template/total:.0%})")

    print("\n--- Manufacturer resolution on holdout ---")
    hits = 0
    for _, row in split.holdout_input.iterrows():
        hints = [
            str(row.get(c) or "")
            for c in ("DIB_Brand", "Unilog_Brand", "E1_Brand")
        ]
        mfr, brand, conf, prov = kb.manufacturers.resolve(
            str(row.get("Part_Manuf") or ""), [h for h in hints if h and "--" not in h]
        )
        if brand:
            hits += 1
    print(f"  brand resolved: {hits}/{total} ({hits/total:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
