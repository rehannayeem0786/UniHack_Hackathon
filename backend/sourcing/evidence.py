"""Retrieved source material, and the citation that travels with it.

Every fact the pipeline takes off the web is wrapped in an `Evidence` object so
the value and the reason to believe it stay attached to one another. That is
what makes the output traceable rather than merely fluent: a reviewer looking at
`Sound Level = 47 dBA` can see the URL it came from, when it was fetched, and
the sentence it was read out of.

`EvidenceBundle` is the per-record collection. It does two jobs:

* builds a token-budgeted prompt context, longest-relevant-first, so a 40-page
  installation manual cannot crowd out the spec table
* answers `supports(value)`, the grounding check — is this exact value actually
  present in the retrieved text? An attribute that passes is marked
  `evidence-verbatim` and scored higher than one the model recalled from
  training. An attribute that fails is not deleted, but it is not promoted
  either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.core.normalize import clean, repair_symbols

# Kinds of source, most authoritative first. Ordering matters: a specification
# sheet outranks a marketing page when both mention the same attribute.
KIND_RANK: dict[str, int] = {
    "specification": 0,
    "manual": 1,
    "product-page": 2,
    "support-page": 3,
    "other": 4,
}

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """Aggressive comparison key: case, punctuation and spacing all ignored."""
    return _NON_ALNUM.sub("", (text or "").casefold())


def _loose(text: str) -> str:
    """Whitespace-normalised, case-folded text for substring searching."""
    return _WS.sub(" ", (text or "").casefold()).strip()


@dataclass
class Evidence:
    """One retrieved document, reduced to text plus its citation."""

    url: str
    kind: str = "other"
    title: str = ""
    text: str = ""
    # label -> value pairs lifted out of HTML specification tables
    tables: dict[str, str] = field(default_factory=dict)
    retrieved_at: str = ""
    from_cache: bool = False
    # Did the document actually mention the part number we asked about?
    mentions_mpn: bool = False
    # Stronger still: is the part number in the URL itself? That is the mark of
    # the product's own page rather than a page that merely references it.
    url_names_part: bool = False
    # Was this read from a reputable third-party source rather than the
    # manufacturer's own site? Third-party evidence supplements a record when
    # the manufacturer publishes nothing, but it is always cited as such and
    # scored below first-party evidence — it can never become the `MFR URL`
    # and it can never outrank the manufacturer's own word.
    third_party: bool = False

    def __post_init__(self) -> None:
        self.retrieved_at = self.retrieved_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

    @property
    def rank(self) -> int:
        return KIND_RANK.get(self.kind, KIND_RANK["other"])

    @property
    def source_tier(self) -> str:
        """`first-party` or `third-party`: the citation's trust tier."""
        return "third-party" if self.third_party else "first-party"

    @property
    def is_pdf(self) -> bool:
        return self.url.lower().split("?")[0].endswith(".pdf")

    def snippet(self, needle: str, width: int = 160) -> str:
        """The sentence-ish window around `needle`, for showing a reviewer."""
        haystack = _loose(self.text)
        found = haystack.find(_loose(needle))
        if found < 0:
            return ""
        start = max(0, found - width // 2)
        return _WS.sub(" ", self.text[start : start + width]).strip()

    def citation(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "kind": self.kind,
            "title": self.title,
            "retrieved_at": self.retrieved_at,
            "from_cache": self.from_cache,
            "characters": len(self.text),
            "table_rows": len(self.tables),
            "source": self.source_tier,
        }

    def as_context(self, budget: int) -> str:
        """Render for a prompt: the spec table first, then prose."""
        tier = "THIRD-PARTY " if self.third_party else ""
        head = f"[{tier}{self.kind}] {self.title or self.url}\nSOURCE: {self.url}\n"
        pieces: list[str] = []
        if self.tables:
            rows = "\n".join(f"  {k}: {v}" for k, v in self.tables.items())
            pieces.append(f"SPECIFICATIONS:\n{rows}")
        if self.text:
            pieces.append(f"TEXT:\n{self.text}")
        body = "\n".join(pieces)
        room = max(0, budget - len(head))
        return head + body[:room]


@dataclass
class EvidenceBundle:
    """Everything retrieved for one product record."""

    documents: list[Evidence] = field(default_factory=list)
    # Domains we were permitted to read, recorded so a run is auditable.
    permitted_domains: list[str] = field(default_factory=list)
    # Human-readable note when retrieval was skipped or found nothing.
    note: str = ""

    def add(self, evidence: Evidence | None) -> None:
        if evidence is None or not (evidence.text or evidence.tables):
            return
        if any(d.url == evidence.url for d in self.documents):
            return
        self.documents.append(evidence)
        # First-party documents outrank third-party ones at the same kind, so a
        # manufacturer spec sheet always sorts ahead of a third-party page.
        self.documents.sort(key=lambda d: (d.third_party, d.rank, -len(d.text)))

    def __bool__(self) -> bool:
        return bool(self.documents)

    def __len__(self) -> int:
        return len(self.documents)

    @property
    def urls(self) -> list[str]:
        return [d.url for d in self.documents]

    @property
    def has_first_party(self) -> bool:
        """True when at least one document came from the manufacturer itself."""
        return any(not d.third_party for d in self.documents)

    @property
    def third_party_documents(self) -> list[Evidence]:
        return [d for d in self.documents if d.third_party]

    def best_product_page(self) -> Evidence | None:
        """The page most suitable as `MFR URL`.

        Ranked on how strongly the page is *about* this part, not on how much
        text it has. A part number in the URL is the strongest signal; length is
        only the final tie-break, because otherwise a long press release that
        happens to name the model beats the product's own page.

        Third-party documents are excluded outright: `MFR URL` is by definition
        the manufacturer's own page, and a reputable third-party listing can
        never stand in for it.
        """
        pages = [
            d
            for d in self.documents
            if not d.third_party
            and not d.is_pdf
            and d.kind in {"product-page", "specification", "support-page"}
        ]
        if not pages:
            return None
        return sorted(
            pages,
            key=lambda d: (
                not d.url_names_part,
                not d.mentions_mpn,
                d.rank,
                -len(d.tables),
                -len(d.text),
            ),
        )[0]

    def documents_of(self, kind: str) -> list[Evidence]:
        return [d for d in self.documents if d.kind == kind]

    # -- grounding ---------------------------------------------------------
    def supports(self, value: str, *, minimum: int = 2) -> Evidence | None:
        """The document containing this value verbatim, if any.

        Comparison ignores case, spacing and punctuation, so `50-1/4 in` matches
        `50 1/4"` in a spec table. Values shorter than `minimum` characters are
        refused outright: a single digit appears in every document and proves
        nothing.
        """
        needle = clean(repair_symbols(value or ""))
        if len(_fold(needle)) < minimum:
            return None
        folded = _fold(needle)
        for document in self.documents:
            if folded in _fold(document.text):
                return document
            if any(folded in _fold(v) for v in document.tables.values()):
                return document
        return None

    def table_lookup(self, label: str) -> tuple[str, Evidence] | None:
        """Find a specification-table row whose label matches, best source first."""
        target = _fold(label)
        if not target:
            return None
        for document in self.documents:
            for key, value in document.tables.items():
                folded = _fold(key)
                if folded == target or (len(target) > 4 and target in folded):
                    if clean(value):
                        return clean(value), document
        return None

    # -- prompting ---------------------------------------------------------
    def merged_tables(self) -> dict[str, tuple[str, str]]:
        """Every specification pair across all sources, best source winning.

        Returned as `label -> (value, source url)` so the pairs can be rendered
        with their provenance and de-duplicated across documents.
        """
        merged: dict[str, tuple[str, str]] = {}
        for document in self.documents:  # already sorted most authoritative first
            for label, value in document.tables.items():
                if label.startswith("_"):
                    continue  # internal keys such as the JSON-LD description
                merged.setdefault(label, (value, document.url))
        return merged

    def as_prompt_context(self, budget: int = 3500) -> str:
        """Build a specifications-first context within a character budget.

        The ordering here was corrected after measurement, and the reason is worth
        recording. Concatenating whole documents put up to 6,000 characters of
        installation-manual prose — largely safety warnings — ahead of the
        attribute template, and attribute fill on retrieved rows fell by roughly a
        third: the model started summarising the source instead of completing the
        template.

        Specification pairs are the signal. They are compact, they are the
        manufacturer's own label/value claims, and they map almost directly onto
        the delivery format's attribute triplets. So they go first and are never
        truncated away; prose gets whatever budget is left, capped per document so
        one long PDF cannot crowd out a short, precise specification page.
        """
        if not self.documents:
            return ""

        blocks: list[str] = []
        remaining = budget

        tables = self.merged_tables()
        if tables:
            rows = "\n".join(f"  {label}: {value}" for label, (value, _) in tables.items())
            # Label the block by where the pairs actually came from: a bundle
            # that only holds third-party documents must not present its tables
            # as the manufacturer's own claims.
            heading = (
                "MANUFACTURER SPECIFICATIONS"
                if self.has_first_party
                else "THIRD-PARTY SPECIFICATIONS (not from the manufacturer)"
            )
            block = f"{heading}:\n{rows[: max(600, budget - 400)]}"
            blocks.append(block)
            remaining -= len(block)

        # Prose is supporting material: it carries features and selling points
        # that never appear in a table, but it is capped hard.
        per_document = max(300, remaining // max(1, len(self.documents)))
        for document in self.documents:
            if remaining <= 200 or not document.text:
                break
            slice_ = document.text[: min(per_document, remaining)]
            tier = "THIRD-PARTY " if document.third_party else ""
            blocks.append(f"[{tier}{document.kind}] SOURCE: {document.url}\n{slice_}")
            remaining -= len(slice_)

        return "\n\n---\n\n".join(blocks)

    def citations(self) -> list[dict[str, Any]]:
        return [d.citation() for d in self.documents]

    def compact(self) -> None:
        """Drop document text once every stage has read it.

        Called at the end of the pipeline for each record. Citations, kinds and
        the specification tables survive, so the UI and the exporter lose
        nothing, but a batch of several hundred records no longer holds every
        PDF it opened in memory. `characters` is preserved in the citation, so
        the size of the evidence read is still reportable after compaction.
        """
        for document in self.documents:
            document.text = ""
            if len(document.tables) > 40:
                document.tables = dict(list(document.tables.items())[:40])

    def summary(self) -> dict[str, Any]:
        return {
            "documents": len(self.documents),
            "kinds": sorted({d.kind for d in self.documents}),
            "from_cache": sum(1 for d in self.documents if d.from_cache),
            "mpn_confirmed": sum(1 for d in self.documents if d.mentions_mpn),
            "characters": sum(len(d.text) for d in self.documents),
            "permitted_domains": self.permitted_domains,
            "third_party_documents": len(self.third_party_documents),
            "note": self.note,
        }
