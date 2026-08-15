"""Taxonomy classification: raw category triple + description -> Classpath.

Strategy is knowledge-base first, LLM second. The registry narrows 62 possible
classpaths down to a handful of candidates for the row's Dept/Class/Fine; the
model only picks between those candidates. It is never asked to invent a
classpath, which is what keeps output inside the approved taxonomy.
"""

from __future__ import annotations

from backend.core.schema import ProductRecord
from backend.knowledge.registry import KnowledgeBase
from backend.knowledge.retrieval import query_text
from backend.llm.client import GeminiClient

# Retrieval replaces the model only on a near-duplicate description. Set high
# on purpose: a cheap wrong answer is worse than a paid right one.
RETRIEVAL_FLOOR = 0.75

SCHEMA = {
    "type": "object",
    "properties": {
        "classpath": {"type": "string"},
        "product_name": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["classpath", "product_name", "confidence"],
}

SYSTEM = (
    "You classify industrial distributor products into a fixed retail taxonomy. "
    "Abbreviated trade descriptions are your main signal: CPLG=coupling, "
    "BRS=brass, SS/SST=stainless steel, SQ=square, RCPT=receptacle, "
    "SW=switch, DX=duplex, GALV=galvanized, CRDLS=cordless."
)


class ClassifierAgent:
    """Assigns each record a classpath and a human-readable product name."""

    name = "classifier"

    def __init__(self, kb: KnowledgeBase, llm: GeminiClient) -> None:
        self.kb = kb
        self.llm = llm

    def run(self, record: ProductRecord) -> ProductRecord:
        candidates = self.kb.taxonomy.candidates(record.dept, record.klass, record.fine)

        if not candidates:
            record.issues.append("no classpath candidates for category triple")
            record.confidence["classpath"] = 0.0
            return record

        # Unambiguous: the training fold only ever mapped this triple one way.
        if len(candidates) == 1:
            record.classpath = candidates[0]
            record.confidence["classpath"] = 0.92
            record.provenance["classpath"] = "registry-unique"
            self._assign_name(record, candidates[0], ask_llm=True)
            return record

        # Retrieval prior: near-identical rows in the training fold already
        # carry the answer, and agreeing neighbours are better evidence than a
        # model guessing from an abbreviation. This also skips the API call.
        query = self._query(record)
        decided = self.kb.retrieval.decide(query, candidates, floor=RETRIEVAL_FLOOR)
        if decided:
            record.classpath = decided.classpath
            record.confidence["classpath"] = 0.9
            record.provenance["classpath"] = f"retrieval:{decided.score:.2f}"
            self._assign_name(record, decided.classpath, ask_llm=True)
            return record

        self._disambiguate(record, candidates, self.kb.retrieval.rank(query, candidates))
        return record

    def _query(self, record: ProductRecord) -> str:
        return query_text(record.raw_description)

    def _assign_name(
        self, record: ProductRecord, classpath: str, *, ask_llm: bool
    ) -> None:
        """Product name from neighbours, then the classpath modal, then the model."""
        retrieved = self.kb.retrieval.product_name_for(self._query(record), classpath)
        if retrieved:
            record.product_name = retrieved
            record.confidence["product_name"] = 0.8
            record.provenance["product_name"] = "retrieval"
            return

        record.product_name = self.kb.taxonomy.suggest_product_name(classpath)
        record.confidence["product_name"] = 0.5
        record.provenance["product_name"] = "classpath-modal"
        if ask_llm and not record.product_name:
            self._name_from_llm(record, [classpath])

    def _disambiguate(
        self,
        record: ProductRecord,
        candidates: list[str],
        ranked: list[tuple[str, float]] | None = None,
    ) -> None:
        # Put the retrieval-preferred candidates first: the model reads a list,
        # and a well-ordered list is easier to choose from.
        if ranked:
            preferred = [c for c, _ in ranked]
            candidates = preferred + [c for c in candidates if c not in set(preferred)]
        options = "\n".join(f"- {c}" for c in candidates[:25])
        prompt = (
            f"Raw product description: {record.raw_description!r}\n"
            f"Manufacturer part number: {record.raw_mpn!r}\n"
            f"Distributor category: {record.dept} > {record.klass} > {record.fine}\n"
            f"Brand hints: {record.brand_hints or 'none'}\n\n"
            f"Choose the single best classpath from this list:\n{options}\n\n"
            "Copy the chosen classpath EXACTLY as written above. "
            "Also give the short generic item type as product_name "
            "(e.g. 'Dishwasher', 'Industrial Surface Cover', 'Cordless Drill') "
            "in Title Case, with no brand and no model number. "
            "confidence is 0..1."
        )
        result = self.llm.generate_json(prompt, SCHEMA, fast=True, system=SYSTEM)

        if not result:
            # Fail soft to the most frequent classpath for this triple.
            chosen = ranked[0][0] if ranked else candidates[0]
            record.classpath = chosen
            record.confidence["classpath"] = 0.45
            record.provenance["classpath"] = "retrieval-fallback" if ranked else "registry-fallback"
            self._assign_name(record, chosen, ask_llm=False)
            record.issues.append("classifier LLM unavailable, fell back to ranked candidate")
            return

        chosen = str(result.get("classpath", "")).strip()
        if chosen not in candidates:
            # Model drifted off the list; snap back to the closest candidate.
            from rapidfuzz import fuzz, process

            best = process.extractOne(chosen, candidates, scorer=fuzz.token_set_ratio)
            chosen = best[0] if best and best[1] >= 60 else candidates[0]
            record.issues.append("classifier returned off-list classpath, snapped to nearest")
            record.confidence["classpath"] = 0.55
        else:
            record.confidence["classpath"] = min(
                float(result.get("confidence", 0.7) or 0.7), 0.95
            )

        record.classpath = chosen
        record.provenance["classpath"] = "llm-choice"

        # A name observed on a near-identical labelled row beats a generated
        # one, because the delivery format's naming is a house convention
        # ("Sanitizing Electric Dryer") rather than a free description.
        retrieved = self.kb.retrieval.product_name_for(self._query(record), chosen)
        name = str(result.get("product_name", "")).strip()
        if retrieved:
            record.product_name = retrieved
            record.confidence["product_name"] = 0.8
            record.provenance["product_name"] = "retrieval"
        else:
            record.product_name = name or self.kb.taxonomy.suggest_product_name(chosen)
            record.confidence["product_name"] = 0.8 if name else 0.4
            record.provenance["product_name"] = "llm" if name else "classpath-modal"

        if reasoning := str(result.get("reasoning", "")).strip():
            record.provenance["classpath_reasoning"] = reasoning[:300]

    def _name_from_llm(self, record: ProductRecord, candidates: list[str]) -> None:
        """Even when the classpath is certain, the product name still needs deriving."""
        if record.product_name:
            return
        prompt = (
            f"Raw product description: {record.raw_description!r}\n"
            f"Classpath: {record.classpath}\n\n"
            "Return the short generic item type as product_name in Title Case "
            "(no brand, no model number). Repeat the classpath unchanged."
        )
        result = self.llm.generate_json(prompt, SCHEMA, fast=True, system=SYSTEM)
        if result and (name := str(result.get("product_name", "")).strip()):
            record.product_name = name
            record.confidence["product_name"] = 0.75
