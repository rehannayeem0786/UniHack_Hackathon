"""Knowledge base learned from labelled delivery rows.

The official reference pack (manufacturer/brand list, cross-category LOV, UOM
standards, decimal-fraction table) is not in this workspace, so the registries
below reconstruct the same lookups from ground-truth rows:

* `TaxonomyRegistry`      Dept/Class/Fine -> Classpath, and Classpath -> ordered
                          attribute template. Replaces the LOV classpath sheet.
* `ManufacturerRegistry`  messy supplier string -> canonical manufacturer/brand.
                          Replaces UniCat_Manufacturer_and_Brand_List.
* `AttributeVocabulary`   (Classpath, Label) -> permitted values and UOM.
                          Replaces Unicat_Lov attribute values.

Critical: only ever fit these on a *training* fold. Fitting on all 200 rows and
then scoring on the same rows produces a meaningless accuracy figure.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from rapidfuzz import fuzz, process

from backend.core.normalize import canonical_uom, clean, repair_symbols
from backend.core.schema import MAX_ATTRIBUTES
from backend.knowledge.assets import AssetRegistry
from backend.knowledge.retrieval import ClasspathRetriever
from backend.knowledge.style import DescriptionStyleRegistry

# --- helpers ----------------------------------------------------------------


def _cell(row: pd.Series | dict[str, Any], key: str) -> str:
    value = row.get(key) if isinstance(row, dict) else row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean(repair_symbols(str(value)))


def _match_key(text: str) -> str:
    """Aggressive normalisation for fuzzy lookup keys."""
    import re

    lowered = repair_symbols(text or "").casefold()
    lowered = re.sub(r"\(.*?\)", " ", lowered)  # drop supplier codes: "(APPDE)"
    lowered = re.sub(
        r"\b(inc|llc|ltd|co|corp|corporation|company|group|holding|holdings|"
        r"mfg|manufacturing|industries|international|usa|na|the|and)\b",
        " ",
        lowered,
    )
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


# Brand names are short, so partial-ratio scorers are dangerous here: WRatio
# scores "ge" against "hager" at 90+ because it is a substring, which once
# resolved a GE dishwasher to a hinge manufacturer. Rules that avoid this:
#   * names of 3 characters or fewer must match a key exactly
#   * fuzzy candidates must be of comparable length to the query
#   * scoring uses whole-string similarity, not partial/substring similarity
#   * a strict prefix is accepted separately, so "frigidaire gallery" can
#     still resolve to the approved "frigidaire"
_SHORT_NAME = 3
_LENGTH_RATIO = 0.55
_FUZZY_FLOOR = 88
_PREFIX_MIN = 6


def _similarity(a: str, b: str) -> float:
    """Whole-string similarity, tolerant of word order and lost spaces."""
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def _safe_match(key: str, table: dict[str, str]) -> str | None:
    """Fuzzy-match `key` against `table` keys without substring false positives."""
    if not key or not table:
        return None

    # Too short to fuzzy-match safely: "ge", "jet" would collide with anything.
    if len(key) <= _SHORT_NAME:
        return key if key in table else None

    # A sub-brand resolving to its parent, e.g. "frigidaire gallery" -> "frigidaire".
    prefix_hits = [
        k for k in table
        if len(k) >= _PREFIX_MIN
        and (key.startswith(k + " ") or k.startswith(key + " "))
    ]
    if prefix_hits:
        return max(prefix_hits, key=len)

    candidates = [
        k for k in table
        if k and min(len(k), len(key)) / max(len(k), len(key)) >= _LENGTH_RATIO
    ]
    if not candidates:
        return None

    best = max(candidates, key=lambda k: _similarity(key, k))
    return best if _similarity(key, best) >= _FUZZY_FLOOR else None


# --- taxonomy ---------------------------------------------------------------


@dataclass
class TaxonomyRegistry:
    """Maps distributor categories onto canonical classpaths and templates."""

    # (dept, class, fine) -> Counter of classpaths
    triple_to_paths: dict[tuple[str, str, str], Counter] = field(default_factory=dict)
    # classpath -> ordered attribute labels (the LOV template)
    templates: dict[str, list[str]] = field(default_factory=dict)
    # leaf node name -> classpath
    leaf_to_path: dict[str, str] = field(default_factory=dict)
    # classpath -> most common Product Name values
    product_names: dict[str, Counter] = field(default_factory=dict)

    def candidates(self, dept: str, klass: str, fine: str) -> list[str]:
        """Classpaths seen for this triple, most frequent first."""
        key = (dept.casefold(), klass.casefold(), fine.casefold())
        counter = self.triple_to_paths.get(key)
        if counter:
            return [path for path, _ in counter.most_common()]

        # Fall back to Dept+Class, then Dept alone, so unseen fine-lines
        # still get a plausible shortlist instead of nothing.
        for depth in (2, 1):
            merged: Counter = Counter()
            for (d, c, _f), paths in self.triple_to_paths.items():
                if depth == 2 and (d, c) == (dept.casefold(), klass.casefold()):
                    merged.update(paths)
                elif depth == 1 and d == dept.casefold():
                    merged.update(paths)
            if merged:
                return [path for path, _ in merged.most_common(25)]
        return []

    def template_for(self, classpath: str) -> list[str]:
        """Ordered attribute labels for a classpath, with a nearest-leaf fallback."""
        if classpath in self.templates:
            return list(self.templates[classpath])

        leaf = classpath.split(">")[-1].strip()
        if leaf in self.leaf_to_path:
            return list(self.templates.get(self.leaf_to_path[leaf], []))

        if self.templates:
            best = process.extractOne(
                classpath, list(self.templates), scorer=fuzz.token_set_ratio
            )
            if best and best[1] >= 70:
                return list(self.templates[best[0]])
        return []

    def suggest_product_name(self, classpath: str) -> str:
        counter = self.product_names.get(classpath)
        return counter.most_common(1)[0][0] if counter else ""

    @property
    def all_classpaths(self) -> list[str]:
        return sorted(self.templates)


# --- manufacturers ----------------------------------------------------------


@dataclass
class ManufacturerRegistry:
    """Resolves messy supplier strings to approved manufacturer/brand pairs."""

    # normalised supplier key -> Counter of (manufacturer, brand)
    supplier_pairs: dict[str, Counter] = field(default_factory=dict)
    # normalised brand key -> canonical brand (with ® / ™ intact)
    brand_forms: dict[str, str] = field(default_factory=dict)
    # normalised manufacturer key -> canonical manufacturer
    manufacturer_forms: dict[str, str] = field(default_factory=dict)
    # canonical brand -> canonical manufacturer
    brand_to_manufacturer: dict[str, str] = field(default_factory=dict)
    # part-number prefix -> the one brand that uses it ("DR7" -> Speed Queen)
    mpn_prefix_brand: dict[str, str] = field(default_factory=dict)
    # description shorthand -> brand ("sq" -> Speed Queen, "milw" -> Milwaukee)
    desc_token_brand: dict[str, str] = field(default_factory=dict)
    # brand strings that pointed at more than one approved brand
    ambiguous_hints: set[str] = field(default_factory=set)

    def resolve(
        self,
        supplier: str,
        brand_hints: Iterable[str] = (),
        *,
        mpn: str = "",
        description: str = "",
    ) -> tuple[str, str, float, str]:
        """Return `(manufacturer, brand, confidence, provenance)`.

        A brand hint from the input row is trusted over the supplier mapping,
        because one distributor code (e.g. a co-op) fronts many real brands.
        """
        # 1. An exact brand hint that only ever meant one brand. This is the
        #    strongest evidence there is.
        ambiguous: list[str] = []
        for hint in brand_hints:
            key = _match_key(hint)
            if not key or key not in self.brand_forms:
                continue
            if key in self.ambiguous_hints:
                ambiguous.append(key)
                continue
            brand = self.brand_forms[key]
            return self.brand_to_manufacturer.get(brand, ""), brand, 0.97, "brand-exact"

        # 2. The part-number prefix. Catalogue numbering is brand-specific and
        #    it breaks ties an ambiguous brand string cannot: Satco's S-series
        #    ships as SATCO, its 65-series as NUVO by SATCO.
        prefix_hit = self._from_mpn(mpn)
        if prefix_hit:
            return prefix_hit

        # 3. The ambiguous hint, now resolved to its most common reading.
        for key in ambiguous:
            brand = self.brand_forms[key]
            return (
                self.brand_to_manufacturer.get(brand, ""),
                brand,
                0.80,
                "brand-exact-ambiguous",
            )

        # 4. Fuzzy brand hint match, length-guarded against substring hits.
        for hint in brand_hints:
            match = _safe_match(_match_key(hint), self.brand_forms)
            if match:
                brand = self.brand_forms[match]
                return (
                    self.brand_to_manufacturer.get(brand, ""),
                    brand,
                    0.88,
                    "brand-fuzzy",
                )

        # 5. Supplier string mapping. Only trustworthy when the supplier maps
        #    to a single pair; co-op distributors map to many.
        key = _match_key(supplier)
        counter = self.supplier_pairs.get(key)
        if not counter and key:
            match = _safe_match(key, {k: k for k in self.supplier_pairs})
            if match:
                counter = self.supplier_pairs[match]

        supplier_result: tuple[str, str, float, str] | None = None
        if counter:
            (manufacturer, brand), hits = counter.most_common(1)[0]
            share = hits / sum(counter.values())
            confidence = 0.9 * share if len(counter) > 1 else 0.9
            supplier_result = (manufacturer, brand, confidence, f"supplier:{share:.2f}")
            if confidence >= 0.75:
                return supplier_result

        # 6. Description shorthand. "SQ Elect Dryer" names Speed Queen in words.
        token_hit = self._from_description(description)
        if token_hit:
            return token_hit

        # 7. Fall back to the ambiguous supplier mapping if that is all we have.
        if supplier_result:
            return supplier_result

        return "", "", 0.0, "unresolved"

    def _from_mpn(self, mpn: str) -> tuple[str, str, float, str] | None:
        """Brand implied by the part-number prefix, longest prefix first."""
        import re

        stem = re.sub(r"[^A-Za-z0-9]+", "", mpn or "").upper()
        for length in range(min(5, len(stem)), 1, -1):
            brand = self.mpn_prefix_brand.get(stem[:length])
            if brand:
                return (
                    self.brand_to_manufacturer.get(brand, ""),
                    brand,
                    0.93,
                    f"mpn-prefix:{stem[:length]}",
                )
        return None

    def _from_description(self, description: str) -> tuple[str, str, float, str] | None:
        """Brand implied by shorthand in the abbreviated description."""
        import re

        for token in re.findall(r"[A-Za-z]{2,}", description or ""):
            brand = self.desc_token_brand.get(token.casefold())
            if brand:
                return (
                    self.brand_to_manufacturer.get(brand, ""),
                    brand,
                    0.84,
                    f"desc-token:{token}",
                )
        return None

    def canonical_brand(self, brand: str) -> str:
        """Snap a generated brand string back onto its approved spelling.

        Returns the input unchanged when nothing matches safely, so an
        off-list brand stays visible rather than being silently replaced.
        """
        key = _match_key(brand)
        if key in self.brand_forms:
            return self.brand_forms[key]
        match = _safe_match(key, self.brand_forms)
        return self.brand_forms[match] if match else clean(brand)

    def canonical_manufacturer(self, manufacturer: str) -> str:
        key = _match_key(manufacturer)
        if key in self.manufacturer_forms:
            return self.manufacturer_forms[key]
        match = _safe_match(key, self.manufacturer_forms)
        return self.manufacturer_forms[match] if match else clean(manufacturer)


# --- brand signals ----------------------------------------------------------

# A signal is only kept when it is nearly unambiguous, because a wrong brand is
# worse than no brand: it propagates into every description and every asset name.
_SIGNAL_PURITY = 0.85
_SIGNAL_SUPPORT = 2

# Fields whose words describe *what a product is*. Any word appearing here is a
# type word, not a brand cue, so "Washer" and "Dryer" are excluded from the
# description index even though one supplier dominates those categories. This is
# derived from the data rather than hand-listed, so it stays correct on a new
# catalogue.
_TYPE_WORD_COLUMNS: tuple[str, ...] = (
    "Product Name", "Dept", "Class", "Fine", "Classpath",
)


def _fit_brand_signals(frame: pd.DataFrame, registry: ManufacturerRegistry) -> None:
    """Learn part-number prefixes and description shorthand that imply a brand."""
    import re

    prefix_votes: dict[str, Counter] = defaultdict(Counter)
    token_votes: dict[str, Counter] = defaultdict(Counter)
    type_words: set[str] = set()
    brand_words: set[str] = set()

    for _, row in frame.iterrows():
        for column in _TYPE_WORD_COLUMNS:
            for word in re.findall(r"[A-Za-z]{2,}", _cell(row, column)):
                type_words.add(word.casefold())
        for word in re.findall(r"[A-Za-z]{2,}", _cell(row, "BRAND_NAME")):
            brand_words.add(word.casefold())
        for index in range(1, MAX_ATTRIBUTES + 1):
            for word in re.findall(r"[A-Za-z]{2,}", _cell(row, f"ATTRIBUTE_VALUE {index}")):
                type_words.add(word.casefold())

    # A word that names a brand outranks its use as a type word: "Trex" is both
    # the brand and half the category name.
    type_words -= brand_words

    for _, row in frame.iterrows():
        brand = _cell(row, "BRAND_NAME")
        if not brand:
            continue

        stem = re.sub(
            r"[^A-Za-z0-9]+", "", _cell(row, "MANUFACTURER_PART_NUMBER")
        ).upper()
        # Numeric prefixes count too: Satco's 65-series is a NUVO line. The
        # purity filter below discards any prefix that is really a size code.
        for length in range(2, 6):
            if len(stem) <= length:
                break
            prefix_votes[stem[:length]][brand] += 1

        for token in re.findall(r"[A-Za-z]{2,}", _cell(row, "Part_Desc")):
            folded = token.casefold()
            if folded in type_words:
                continue
            if folded in stem.casefold():
                continue  # part of the part number, not a brand cue
            token_votes[folded][brand] += 1

    registry.mpn_prefix_brand = _pure_winners(prefix_votes)
    registry.desc_token_brand = _pure_winners(token_votes)


def _pure_winners(votes: dict[str, Counter]) -> dict[str, str]:
    """Keep only keys that point at one brand with enough, consistent evidence."""
    out: dict[str, str] = {}
    for key, counter in votes.items():
        total = sum(counter.values())
        brand, hits = counter.most_common(1)[0]
        if hits >= _SIGNAL_SUPPORT and hits / total >= _SIGNAL_PURITY:
            out[key] = brand
    return out


# --- attribute vocabulary ---------------------------------------------------


# Labels that are open-ended by nature: no controlled list can cover them.
_FREE_TEXT_LABELS: tuple[str, ...] = (
    "additional information", "dimension", "size", "model", "series",
    "description", "note", "capacity", "includes", "part number",
)


@dataclass
class AttributeVocabulary:
    """Permitted values and units per (classpath, attribute label)."""

    values: dict[tuple[str, str], Counter] = field(default_factory=dict)
    uoms: dict[tuple[str, str], Counter] = field(default_factory=dict)
    label_values: dict[str, Counter] = field(default_factory=dict)

    def permitted(self, classpath: str, label: str, limit: int = 40) -> list[str]:
        """Values seen for this attribute — the constrained vocabulary."""
        counter = self.values.get((classpath, label.casefold()))
        if not counter:
            counter = self.label_values.get(label.casefold())
        if not counter:
            return []
        return [value for value, _ in counter.most_common(limit)]

    def expected_uom(self, classpath: str, label: str) -> str:
        counter = self.uoms.get((classpath, label.casefold()))
        if not counter:
            return ""
        return counter.most_common(1)[0][0]

    def is_free_text(self, classpath: str, label: str) -> bool:
        """True when an attribute behaves as free text rather than a value list.

        Judging this from a small sample needs care. Three distinct values in
        three rows is not a vocabulary of three, it is evidence that every
        product has its own value. So an attribute is treated as free text when
        its values are almost all unique, when the sample is too small to
        constrain, or when the label is inherently open-ended.
        """
        folded = label.casefold()
        if any(hint in folded for hint in _FREE_TEXT_LABELS):
            return True

        counter = self.values.get((classpath, folded))
        if not counter:
            return True

        total = sum(counter.values())
        distinct = len(counter)

        if total < 3:
            return True  # too little evidence to call anything a violation
        if distinct == 1:
            return False  # a genuine single-value list
        return distinct / total >= 0.9


# --- the bundle -------------------------------------------------------------


@dataclass
class KnowledgeBase:
    taxonomy: TaxonomyRegistry
    manufacturers: ManufacturerRegistry
    attributes: AttributeVocabulary
    style: "DescriptionStyleRegistry" = field(
        default_factory=lambda: DescriptionStyleRegistry()
    )
    assets: "AssetRegistry" = field(default_factory=lambda: AssetRegistry())
    retrieval: "ClasspathRetriever" = field(
        default_factory=lambda: ClasspathRetriever()
    )
    approvals: list[str] = field(default_factory=list)
    # Brand -> the approval set that dominates that brand's labelled rows.
    # Only stored when one set wins on >= 80% of >= 3 rows, so it is a learned
    # brand convention (like brand->manufacturer), never a guess. Applied to
    # records whose own source did not state any certification.
    brand_approvals: dict[str, list[str]] = field(default_factory=dict)
    fitted_rows: int = 0

    # -- construction --
    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "KnowledgeBase":
        """Learn every registry from labelled delivery rows."""
        taxonomy = TaxonomyRegistry(
            triple_to_paths=defaultdict(Counter),
            templates={},
            leaf_to_path={},
            product_names=defaultdict(Counter),
        )
        manufacturers = ManufacturerRegistry(supplier_pairs=defaultdict(Counter))
        vocabulary = AttributeVocabulary(
            values=defaultdict(Counter),
            uoms=defaultdict(Counter),
            label_values=defaultdict(Counter),
        )
        approvals: Counter = Counter()
        template_votes: dict[str, Counter] = defaultdict(Counter)
        brand_form_votes: dict[str, Counter] = defaultdict(Counter)
        # One approval-set observation per labelled row, grouped by brand, so a
        # brand's dominant certification set can be learned below.
        brand_approval_sets: dict[str, list[frozenset[str]]] = defaultdict(list)

        for _, row in frame.iterrows():
            classpath = _cell(row, "Classpath")
            dept, klass, fine = (_cell(row, k) for k in ("Dept", "Class", "Fine"))

            if classpath:
                taxonomy.triple_to_paths[
                    (dept.casefold(), klass.casefold(), fine.casefold())
                ][classpath] += 1
                leaf = classpath.split(">")[-1].strip()
                taxonomy.leaf_to_path.setdefault(leaf, classpath)
                name = _cell(row, "Product Name")
                if name:
                    taxonomy.product_names[classpath][name] += 1

            # ordered attribute template + value vocabulary
            labels: list[str] = []
            for index in range(1, MAX_ATTRIBUTES + 1):
                label = _cell(row, f"ATTRIBUTE_LABEL {index}")
                if not label:
                    continue
                labels.append(label)
                value = _cell(row, f"ATTRIBUTE_VALUE {index}")
                uom = canonical_uom(_cell(row, f"ATTRIBUTE_UOM {index}"))
                if value:
                    vocabulary.values[(classpath, label.casefold())][value] += 1
                    vocabulary.label_values[label.casefold()][value] += 1
                if uom:
                    vocabulary.uoms[(classpath, label.casefold())][uom] += 1
            if classpath and labels:
                template_votes[classpath][tuple(labels)] += 1

            # manufacturer / brand pairs
            manufacturer = _cell(row, "MANUFACTURER_NAME")
            brand = _cell(row, "BRAND_NAME")
            supplier = _cell(row, "Part_Manuf")
            if manufacturer or brand:
                if supplier:
                    manufacturers.supplier_pairs[_match_key(supplier)][
                        (manufacturer, brand)
                    ] += 1
                if brand:
                    brand_form_votes[_match_key(brand)][brand] += 1
                    if manufacturer:
                        manufacturers.brand_to_manufacturer.setdefault(brand, manufacturer)
                if manufacturer:
                    manufacturers.manufacturer_forms.setdefault(
                        _match_key(manufacturer), manufacturer
                    )
                # Brand hints from the raw row also point at the canonical brand.
                # These are votes, not assignments: one supplier brand string can
                # front two approved brands (Satco ships SATCO and NUVO by SATCO),
                # and a hint like that must be recognised as ambiguous.
                for hint_col in ("DIB_Brand", "E1_Brand", "Unilog_Brand"):
                    hint = _cell(row, hint_col)
                    if hint and brand:
                        brand_form_votes[_match_key(hint)][brand] += 1

            for token in _cell(row, "Standard/Approvals").split("|"):
                token = token.strip()
                if token:
                    approvals[token] += 1

            if brand:
                brand_approval_sets[brand].append(
                    frozenset(
                        t.strip()
                        for t in _cell(row, "Standard/Approvals").split("|")
                        if t.strip()
                    )
                )

        # Resolve each brand string to its most common canonical form, and
        # remember which strings were never decisive.
        manufacturers.brand_forms = {
            key: counter.most_common(1)[0][0] for key, counter in brand_form_votes.items()
        }
        manufacturers.ambiguous_hints = {
            key for key, counter in brand_form_votes.items() if len(counter) > 1
        }

        _fit_brand_signals(frame, manufacturers)

        # Learn each brand's dominant certification set. The bar is deliberately
        # high — one set on >= 80% of >= 3 rows — so only a genuine brand
        # convention is stored (United Window & Door rows all carry the same
        # ENERGY STAR/NFRC pair). A brand whose rows disagree is left out, and an
        # empty dominant set is never stored: filling a blank the reference left
        # blank would be worse than leaving it.
        brand_approvals: dict[str, list[str]] = {}
        for brand, sets in brand_approval_sets.items():
            if len(sets) < 3:
                continue
            top, count = Counter(sets).most_common(1)[0]
            if not top or count / len(sets) < 0.8:
                continue
            brand_approvals[brand] = sorted(top)

        # The winning template per classpath is the longest common ordering;
        # take the most frequent, then union in any labels it missed.
        for classpath, votes in template_votes.items():
            best = max(votes.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            merged = list(best)
            for sequence in votes:
                for label in sequence:
                    if label not in merged:
                        merged.append(label)
            taxonomy.templates[classpath] = merged

        return cls(
            taxonomy=taxonomy,
            manufacturers=manufacturers,
            attributes=vocabulary,
            style=DescriptionStyleRegistry.fit(frame),
            assets=AssetRegistry.fit(frame),
            retrieval=ClasspathRetriever.fit(frame),
            approvals=[a for a, _ in approvals.most_common()],
            brand_approvals=brand_approvals,
            fitted_rows=len(frame),
        )

    # -- persistence --
    def save(self, path: Path) -> None:
        """Write a human-readable snapshot so the KB is inspectable and diffable."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fitted_rows": self.fitted_rows,
            "classpaths": self.taxonomy.all_classpaths,
            "templates": self.taxonomy.templates,
            "triples": {
                "|".join(key): dict(counter)
                for key, counter in self.taxonomy.triple_to_paths.items()
            },
            "product_names": {
                key: dict(counter) for key, counter in self.taxonomy.product_names.items()
            },
            "brands": self.manufacturers.brand_to_manufacturer,
            "supplier_pairs": {
                key: {f"{m}||{b}": n for (m, b), n in counter.items()}
                for key, counter in self.manufacturers.supplier_pairs.items()
            },
            "attribute_values": {
                f"{cp}||{label}": dict(counter)
                for (cp, label), counter in self.attributes.values.items()
            },
            "approvals": self.approvals,
            "description_style": self.style.to_payload(),
            "assets": self.assets.to_payload(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- validation --
    def plausible_series(self, classpath: str | None, series: str) -> str:
        """Accept a series only if the vocabulary or its shape supports it.

        Asked for a series, a model will happily return a fragment of the input
        description: `DR7004BE SQ Elect Dryer Bk` yields "SQ Elect". A wrong
        series is expensive because it is written into four of the five
        descriptions, so the bar is an approved value or a recognisable
        series-like shape, and nothing else.
        """
        candidate = clean(series)
        if not candidate:
            return ""

        permitted = self.attributes.permitted(classpath or "", "Series", limit=200)
        if candidate in permitted:
            return candidate

        if permitted:
            best = process.extractOne(candidate, permitted, scorer=fuzz.WRatio)
            if best and best[1] >= 90:
                return best[0]
            # This category has a known set of series and the answer is not in
            # it. Reject rather than fall through to a shape check: a model asked
            # for a series will offer a generic-sounding "Professional Series"
            # for a Leviton GFCI whose real line is "SmartlockPro Series", and
            # that invention would be written into four descriptions.
            return ""

        # No vocabulary for this category at all, so shape is the only evidence
        # available. Accept the conventional naming forms only.
        tail = candidate.rsplit(" ", 1)[-1].casefold()
        if tail in {"series", "line", "collection", "family"} and len(candidate) > 6:
            return candidate
        return ""

    # -- reporting --
    def summary(self) -> dict[str, int]:
        return {
            "fitted_rows": self.fitted_rows,
            "classpaths": len(self.taxonomy.templates),
            "category_triples": len(self.taxonomy.triple_to_paths),
            "attribute_templates": len(self.taxonomy.templates),
            "distinct_attribute_labels": len(self.attributes.label_values),
            "attribute_value_entries": sum(
                len(c) for c in self.attributes.values.values()
            ),
            "brands": len(self.manufacturers.brand_forms),
            "manufacturers": len(self.manufacturers.manufacturer_forms),
            "approvals": len(self.approvals),
            **self.style.summary(),
            **self.assets.summary(),
            **self.retrieval.summary(),
        }
