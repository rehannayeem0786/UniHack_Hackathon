"""Print the description grammar the pipeline learned from the training fold.

The five description surfaces are not written by hand-coded rules, they are
mined from the labelled rows. This prints what was inferred so a reviewer can
check the inference instead of taking it on trust: which attributes each surface
includes, in what order, and how each value is written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.knowledge.datasets import load_split
from backend.knowledge.registry import KnowledgeBase
from backend.knowledge.style import SURFACES


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-ratio", type=float, default=0.3)
    parser.add_argument("--category", default="", help="substring of a classpath")
    parser.add_argument("--limit", type=int, default=3, help="categories to show")
    parser.add_argument("--abbreviations", action="store_true")
    args = parser.parse_args()

    split = load_split(holdout_ratio=args.holdout_ratio)
    kb = KnowledgeBase.fit(split.train_truth)
    style = kb.style

    print(f"fitted on {kb.fitted_rows} training rows")
    for key, value in style.summary().items():
        print(f"  {key:24} {value}")

    classpaths = sorted({cp for cp, _ in style.styles})
    if args.category:
        classpaths = [c for c in classpaths if args.category.casefold() in c.casefold()]
    classpaths = classpaths[: args.limit]

    for classpath in classpaths:
        print("\n" + "=" * 92)
        print(classpath)
        template = kb.taxonomy.template_for(classpath)
        for surface in SURFACES:
            surface_style = style.style(classpath, surface)
            order = style.build_order(classpath, surface, template)
            print(f"\n  [{surface}]  learned from {surface_style.rows} row(s)")
            if not order:
                print("      (no attributes on this surface)")
            for label in order:
                separator, suffix = surface_style.suffixes.get(label, (" ", ""))
                shown = f"<value>{separator}{suffix}" if suffix else "<value>"
                share = surface_style.inclusion.get(label, 0.0)
                print(f"      {share:4.2f}  {label:34} -> {shown}")

    if args.abbreviations:
        print("\n" + "=" * 92)
        print(f"invoice abbreviation lexicon ({len(style.abbreviations)} entries)")
        for word, short in sorted(style.abbreviations.items()):
            print(f"   {word:24} -> {short}")


if __name__ == "__main__":
    main()
