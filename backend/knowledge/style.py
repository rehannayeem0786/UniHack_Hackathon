"""Learned description grammar.

The delivery format rewrites the same product five times, and each surface has
its own house rules. Reading the labelled rows closely shows the rules are not
free prose at all — they are a per-category grammar:

    LONG_DESC1   Southwire(R) Industrial Surface Cover, Duplex Receptacle,
                 Toggle Switch Cover, Square Box, Steel, Galvanized, Silver,
                 4 in Length, 4 in Width, Additional Information: 1/2 in Raised
    SHORT_DESC   Southwire(R) G1941-UPC Industrial Surface Cover, Square,
                 Duplex Receptacle Cover, Steel, Galvanized, 4 in L, 4 in W

Three things vary and all three are observable:

1. **Which** attributes appear on a surface. Voltage and amperage belong in the
   long description but never in the retail line.
2. **What order** they appear in. It is not the attribute-template order:
   `Box Type` precedes `Cover Type` on the invoice line but follows it in the
   template.
3. **How** each value is written. The label is appended as a *suffix* to the
   value, and which part of the label survives is category specific:
   `Cover Type=Duplex Receptacle` becomes `Duplex Receptacle Cover`,
   `Length=4 in` becomes `4 in Length` on the long surface but `4 in L` on the
   short one, and `Voltage Rating=120 V` drops the label entirely.

Hardcoding that grammar for 62 classpaths is not feasible and would not
generalise. Instead this module *mines* it from the training fold: for every
attribute value, it searches the ground-truth sentence for the value followed
by a candidate rendering of its own label, and votes on what it finds. The
result is a per-classpath, per-surface build order and render style, learned
rather than guessed — and it is fitted on the training fold only, so measuring
against the holdout stays honest.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable

import pandas as pd

from backend.core.normalize import clean, repair_symbols

# The five rewritten surfaces and the delivery column each one lives in.
SURFACE_COLUMNS: dict[str, str] = {
    "long": "LONG_DESC1",
    "short": "SHORT_DESC",
    "retail": "RETAIL_DESC",
    "mobile": "MOBILE_DESC",
    "invoice": "INVOICE_DESC",
}

SURFACES: tuple[str, ...] = tuple(SURFACE_COLUMNS)

# Attributes handled by the head/tail of the formula rather than the body.
STRUCTURAL_LABELS: frozenset[str] = frozenset(
    {"series", "additional information"}
)

# Words dropped when shortening a label to its suffix form. Anything ending in
# these carries no meaning once the value is present: `Mounting Type=Leg`
# reads as `Leg Mounting`, not `Leg Mounting Type`.
_FILLER_TAIL: frozenset[str] = frozenset({"type", "rating", "capacity", "size"})

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _singular(word: str) -> str:
    """Crude singulariser: enough for `Cycles`->`Cycle`, safe on `Stainless`."""
    lowered = word.casefold()
    if lowered.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if lowered.endswith(("ss", "us", "is")):
        return word
    if lowered.endswith("es") and len(word) > 4:
        return word[:-2]
    if lowered.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _plural(word: str) -> str:
    """Crude pluraliser, the inverse case: `Cycle`->`Cycles`."""
    lowered = word.casefold()
    if lowered.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if lowered.endswith("y") and len(word) > 2 and word[-2].casefold() not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def _suffix_candidates(label: str) -> list[str]:
    """Ways a label might be written after its value, longest first.

    For `Number of Wash Cycles` this yields `Number of Wash Cycles`,
    `Wash Cycles`, `Wash Cycle`, `Cycles`, `Cycle`, `Wash`, ... and finally the
    empty string, which means the label is not written at all.
    """
    words = _WORD_RE.findall(label)
    if not words:
        return [""]

    seen: set[str] = set()
    out: list[str] = []

    def add(text: str) -> None:
        text = text.strip()
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(text)

    # Every contiguous run of label words, longest first. This is what captures
    # `Cover Type` -> `Cover` and `Battery Capacity` -> `Battery`.
    for length in range(len(words), 0, -1):
        for start in range(0, len(words) - length + 1):
            run = words[start : start + length]
            # Drop a trailing filler word: `Mounting Type` -> `Mounting`.
            if len(run) > 1 and run[-1].casefold() in _FILLER_TAIL:
                add(" ".join(run[:-1]))
            add(" ".join(run))
            add(" ".join(run[:-1] + [_singular(run[-1])]))
            add(" ".join(run[:-1] + [_plural(run[-1])]))

    # Single-letter forms used on the compact surfaces: `4 in L`, `4 in W`.
    if len(words) == 1:
        add(words[0][0].upper())
    else:
        add("".join(w[0].upper() for w in words))

    add("")
    return out


# Separators seen between a value and its label suffix. `5 Wash Cycles` on the
# long surface becomes `5-Wash Cycle` on the short one.
_SEPARATORS: tuple[str, ...] = (" ", "-", "")


def _find_render(text: str, value: str, label: str) -> tuple[int, str, str] | None:
    """Locate `value` in `text` and infer how its label was written after it.

    Returns `(offset, separator, suffix)`, preferring the longest suffix that
    genuinely occurs. Testing candidate suffixes rather than capturing whatever
    follows the value is what keeps `Length=4 in` and `Width=4 in` apart: both
    values are the string `4 in`, but only one is followed by `Width`.
    """
    if not value or not text:
        return None

    haystack = text.casefold()
    needle = value.casefold()

    for suffix in _suffix_candidates(label):
        for separator in _SEPARATORS:
            if not suffix and separator != " ":
                continue  # the empty suffix has no separator to vary
            probe = f"{needle}{separator}{suffix}".casefold() if suffix else needle
            offset = haystack.find(probe)
            if offset < 0:
                continue
            if suffix:
                # Reject a partial word hit: `4 in L` must not match `4 in Length`.
                end = offset + len(probe)
                if end < len(haystack) and haystack[end].isalnum():
                    continue
                return offset, separator, suffix
            return offset, " ", ""
    return None


@dataclass
class SurfaceStyle:
    """How one surface writes the body of one category's description."""

    # label -> (separator, suffix) used when rendering the value
    suffixes: dict[str, tuple[str, str]] = field(default_factory=dict)
    # label -> fraction of rows where the attribute appeared on this surface
    inclusion: dict[str, float] = field(default_factory=dict)
    # label -> median position, used to rebuild the build order
    rank: dict[str, float] = field(default_factory=dict)
    rows: int = 0
    # False for the cross-category average, which must not dictate build order.
    specific: bool = True

    def order(self, threshold: float = 0.5) -> list[str]:
        """Labels this surface includes, in the order it writes them."""
        chosen = [
            label
            for label, share in self.inclusion.items()
            if share >= threshold and label not in STRUCTURAL_LABELS
        ]
        return sorted(chosen, key=lambda label: self.rank.get(label, 1e9))

    def render(self, label: str, value: str) -> str:
        """Write `value` the way this surface writes that label."""
        if not value:
            return ""
        separator, suffix = self.suffixes.get(label.casefold(), (" ", ""))
        return f"{value}{separator}{suffix}".strip() if suffix else value

    def includes(self, label: str, threshold: float = 0.5) -> bool:
        return self.inclusion.get(label.casefold(), 0.0) >= threshold


@dataclass
class DescriptionStyleRegistry:
    """Per-classpath, per-surface description grammar learned from labels."""

    styles: dict[tuple[str, str], SurfaceStyle] = field(default_factory=dict)
    fallback: dict[str, SurfaceStyle] = field(default_factory=dict)
    # Uppercase word -> approved invoice abbreviation, mined from invoice lines.
    abbreviations: dict[str, str] = field(default_factory=dict)

    def style(self, classpath: str | None, surface: str) -> SurfaceStyle:
        """Style for this category, degrading to the cross-category average."""
        if classpath:
            found = self.styles.get((classpath, surface))
            if found and found.rows:
                return found
        return self.fallback.get(surface, SurfaceStyle(specific=False))

    def render(self, classpath: str | None, surface: str, label: str, value: str) -> str:
        return self.style(classpath, surface).render(label, value)

    def build_order(
        self, classpath: str | None, surface: str, template: list[str]
    ) -> list[str]:
        """Labels to write on this surface, in order.

        A category we have seen dictates its own order. For an unseen category
        the cross-category average knows *how* to write each label but nothing
        useful about ordering, so the attribute template order is used instead
        and only labels the average says belong on this surface are kept.
        """
        style = self.style(classpath, surface)
        folded_template = [
            label.casefold()
            for label in template
            if label.casefold() not in STRUCTURAL_LABELS
        ]

        # The long description is the complete record, so an attribute we have
        # no evidence about belongs there. The compact surfaces are selective,
        # so silence means leave it out.
        default = 1.0 if surface == "long" else 0.0

        if style.specific and style.rows:
            learned = style.order()
            if learned:
                return _merge_order(learned, folded_template, style, default)

        # An unseen category: the average knows how to write each label but
        # nothing about ordering, so follow the attribute template.
        return [
            label
            for label in folded_template
            if style.inclusion.get(label, default) >= 0.4
        ]

    def abbreviate(self, text: str) -> str:
        """Apply the mined house abbreviations to an uppercased string."""
        if not text:
            return ""
        out: list[str] = []
        for token in text.upper().split():
            bare = "".join(c for c in token if c.isalnum())
            replacement = self.abbreviations.get(bare)
            out.append(token.replace(bare, replacement) if replacement else token)
        return " ".join(out)

    # -- fitting ------------------------------------------------------------
    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "DescriptionStyleRegistry":
        """Mine the grammar from labelled delivery rows.

        Two passes are required. The invoice line is written in abbreviated
        capitals, so an attribute value cannot be located in it until the
        abbreviation lexicon exists; the first pass builds that lexicon and the
        second uses it.
        """
        rows = [_row_facts(row) for _, row in frame.iterrows()]

        # Pass 1 - mine the abbreviation lexicon from the invoice lines.
        abbrev_votes: dict[str, Counter] = defaultdict(Counter)
        for facts in rows:
            if not facts.invoice:
                continue
            pool = list(_WORD_RE.findall(facts.product_name))
            for _label, rendered in facts.attributes:
                pool += _WORD_RE.findall(rendered)
            _mine_abbreviations(facts.invoice, pool, abbrev_votes)

        # Seed with the house abbreviations we already encode, then let anything
        # actually observed in this catalogue override them.
        from backend.core.normalize import INVOICE_ABBREV

        abbreviations = {
            word: short
            for word, short in INVOICE_ABBREV.items()
            if " " not in word and word != short
        }
        abbreviations.update(
            {
                word: votes.most_common(1)[0][0]
                for word, votes in abbrev_votes.items()
                if _plausible_abbreviation(word, votes.most_common(1)[0][0])
            }
        )

        # Pass 2 - learn per-surface inclusion, order and render style.
        suffix_votes: dict[tuple[str, str], dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        offsets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        hits: dict[tuple[str, str], Counter] = defaultdict(Counter)
        # Rows where the attribute actually had a value. This, not the row
        # count, is the denominator for inclusion: an attribute that was never
        # populated tells us nothing about whether the surface wants it.
        filled: dict[tuple[str, str], Counter] = defaultdict(Counter)
        rows_seen: dict[tuple[str, str], int] = defaultdict(int)

        for facts in rows:
            for surface, column in SURFACE_COLUMNS.items():
                text = facts.surfaces.get(surface, "")
                if not text:
                    continue
                key = (facts.classpath, surface)
                rows_seen[key] += 1
                for label, rendered in facts.attributes:
                    folded = label.casefold()
                    filled[key][folded] += 1
                    if surface == "invoice":
                        # The invoice never appends labels; only presence and
                        # order are learnable, via the abbreviated value.
                        offset = _find_abbreviated(text, rendered, abbreviations)
                        if offset is None:
                            continue
                        suffix_votes[key][folded][(" ", "")] += 1
                        offsets[key][folded].append(float(offset))
                        hits[key][folded] += 1
                        continue

                    found = _find_render(text, rendered, label)
                    if not found:
                        continue
                    offset, separator, suffix = found
                    suffix_votes[key][folded][(separator, suffix)] += 1
                    offsets[key][folded].append(float(offset))
                    hits[key][folded] += 1

        styles: dict[tuple[str, str], SurfaceStyle] = {}
        for key in rows_seen:
            style = SurfaceStyle(rows=rows_seen[key])
            for label, seen in filled[key].items():
                style.inclusion[label] = round(hits[key][label] / (seen or 1), 3)
            for label, votes in suffix_votes.get(key, {}).items():
                style.suffixes[label] = votes.most_common(1)[0][0]
                style.rank[label] = median(offsets[key][label])
            styles[key] = style

        return cls(
            styles=styles,
            fallback=_average_styles(styles),
            abbreviations=abbreviations,
        )

    # -- persistence --------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        return {
            "surfaces": {
                f"{cp}||{surface}": {
                    "rows": style.rows,
                    "order": style.order(),
                    "suffixes": {k: list(v) for k, v in style.suffixes.items()},
                    "inclusion": style.inclusion,
                }
                for (cp, surface), style in sorted(self.styles.items())
            },
            "abbreviations": dict(sorted(self.abbreviations.items())),
        }

    def summary(self) -> dict[str, int]:
        return {
            "styled_surfaces": len(self.styles),
            "styled_categories": len({cp for cp, _ in self.styles}),
            "mined_abbreviations": len(self.abbreviations),
        }


# --- helpers ---------------------------------------------------------------


def _cell(row: pd.Series, key: str) -> str:
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean(repair_symbols(str(value)))


@dataclass
class _RowFacts:
    """One labelled row reduced to what the miner needs, parsed once."""

    classpath: str = ""
    product_name: str = ""
    invoice: str = ""
    surfaces: dict[str, str] = field(default_factory=dict)
    attributes: list[tuple[str, str]] = field(default_factory=list)


def _row_facts(row: pd.Series) -> _RowFacts:
    from backend.core.schema import MAX_ATTRIBUTES

    attributes: list[tuple[str, str]] = []
    for index in range(1, MAX_ATTRIBUTES + 1):
        label = _cell(row, f"ATTRIBUTE_LABEL {index}")
        if not label:
            continue
        value = _cell(row, f"ATTRIBUTE_VALUE {index}")
        if not value:
            continue
        uom = _cell(row, f"ATTRIBUTE_UOM {index}")
        attributes.append((label, f"{value} {uom}".strip() if uom else value))

    surfaces = {s: _cell(row, column) for s, column in SURFACE_COLUMNS.items()}
    return _RowFacts(
        classpath=_cell(row, "Classpath"),
        product_name=_cell(row, "Product Name"),
        invoice=surfaces.get("invoice", ""),
        surfaces=surfaces,
        attributes=attributes,
    )


def _merge_order(
    learned: list[str], template: list[str], style: SurfaceStyle, default: float
) -> list[str]:
    """Splice template-only labels into the learned order at the right place.

    A label the template defines but this category has never been observed
    writing still needs a position. Appending it at the end would put
    `Maximum Height` after `Color`, so instead it inherits the position of the
    nearest label before it in the template that *was* observed.
    """
    position: dict[str, float] = {label: float(i) for i, label in enumerate(learned)}

    cursor = -1.0
    gap = 0
    for label in template:
        if label in position:
            cursor = position[label]
            gap = 0
            continue
        # Never seen on this surface for this category, and neither the record
        # nor the surface default argues for it: leave it out.
        if style.inclusion.get(label, default) < 0.4:
            continue
        gap += 1
        position[label] = cursor + gap / (len(template) + 1.0)

    return sorted(position, key=lambda label: position[label])


def _plausible_abbreviation(word: str, short: str) -> bool:
    """Filter mining noise: `120`->`12` and `FIRST`->`FT` are coincidences."""
    if len(short) < 3 or len(word) < 5:
        return False
    if not word.isalpha() or not short.isalpha():
        return False
    # An abbreviation that saves nothing, or almost everything, is suspect.
    return 0.3 <= len(short) / len(word) <= 0.85


def _find_abbreviated(
    invoice: str, rendered: str, abbreviations: dict[str, str]
) -> int | None:
    """Position of an attribute value inside an abbreviated invoice line.

    The invoice writes `Stainless Steel` as `SST` and `Duplex Receptacle` as
    `DX RCPT`, so the value is located by its first significant word in either
    full or abbreviated form.
    """
    haystack = invoice.upper()
    tokens = set(_WORD_RE.findall(haystack))
    for word in _WORD_RE.findall(rendered.upper()):
        if len(word) < 2:
            continue
        for form in (word, abbreviations.get(word, "")):
            if form and form in tokens:
                match = re.search(rf"\b{re.escape(form)}\b", haystack)
                if match:
                    return match.start()
    return None


def _average_styles(
    styles: dict[tuple[str, str], SurfaceStyle]
) -> dict[str, SurfaceStyle]:
    """Cross-category fallback: how most categories write each label."""
    merged: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    ranks: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    inclusion: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for (_classpath, surface), style in styles.items():
        for label, choice in style.suffixes.items():
            merged[surface][label][choice] += style.rows or 1
            ranks[surface][label].append(style.rank.get(label, 1e9))
            inclusion[surface][label].append(style.inclusion.get(label, 0.0))

    out: dict[str, SurfaceStyle] = {}
    for surface, labels in merged.items():
        style = SurfaceStyle(rows=1, specific=False)
        for label, votes in labels.items():
            style.suffixes[label] = votes.most_common(1)[0][0]
            style.rank[label] = median(ranks[surface][label])
            shares = inclusion[surface][label]
            style.inclusion[label] = round(sum(shares) / len(shares), 3)
        out[surface] = style
    return out


def _is_abbreviation(short: str, full: str) -> bool:
    """True when `short` looks like a clipped form of `full` (SURF of SURFACE)."""
    if len(short) >= len(full) or not short or not full:
        return False
    if short[0] != full[0]:
        return False
    # Every letter of the abbreviation must appear in order in the full word.
    position = 0
    for character in short:
        position = full.find(character, position)
        if position < 0:
            return False
        position += 1
    return True


def _mine_abbreviations(
    invoice: str, pool: Iterable[str], votes: dict[str, Counter]
) -> None:
    """Align invoice tokens against known words to learn the house abbreviations."""
    tokens = [t for t in _WORD_RE.findall(invoice.upper()) if len(t) > 1]
    words = {w.upper() for w in pool if len(w) > 2}
    if not tokens or not words:
        return

    for token in tokens:
        if token in words:
            continue  # written in full, nothing to learn
        matches = [w for w in words if _is_abbreviation(token, w)]
        if len(matches) == 1:
            votes[matches[0]][token] += 1
