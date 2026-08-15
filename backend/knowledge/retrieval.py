"""Nearest-neighbour retrieval over labelled rows.

The classifier's candidate list contains the correct classpath essentially
always, so the hard part is not recall, it is choosing. A language model asked
to pick from twenty-five similar category paths using a fifteen-character
abbreviation has very little to go on.

Retrieval has much more. `DR7004BE SQ Elect Dryer Bk` is textually almost
identical to rows already labelled in the training fold, and those rows carry
the answer. So this module indexes every labelled row by its category triple
plus raw description and votes on the classpath and product name of the closest
matches.

Character n-grams rather than words, because the descriptions are abbreviated
and inconsistently spaced: `Elect`, `Electric` and `Elec` share n-grams but no
whole words. The index costs nothing to build, needs no API call, and where it
is confident it replaces an LLM call outright — which is what makes a
thousand-row catalogue affordable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backend.core.normalize import clean, repair_symbols


def _cell(row: pd.Series, key: str) -> str:
    value = row.get(key)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean(repair_symbols(str(value)))


def query_text(description: str) -> str:
    """The text form used for both indexing and lookup.

    Deliberately the raw description and nothing else. The candidate list is
    already derived from the category triple, so including the triple here would
    make every row in a category look alike and swamp the one field that
    actually discriminates between sibling classpaths.
    """
    return clean(description).casefold()


@dataclass
class Neighbour:
    classpath: str
    product_name: str
    score: float


@dataclass
class ClasspathRetriever:
    """TF-IDF nearest-neighbour index over labelled rows."""

    classpaths: list[str] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    _vectorizer: Any = None
    _matrix: Any = None

    @property
    def fitted(self) -> bool:
        return self._matrix is not None and bool(self.classpaths)

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "ClasspathRetriever":
        texts: list[str] = []
        classpaths: list[str] = []
        names: list[str] = []

        for _, row in frame.iterrows():
            classpath = _cell(row, "Classpath")
            if not classpath:
                continue
            text = query_text(_cell(row, "Part_Desc"))
            if not text:
                continue
            texts.append(text)
            classpaths.append(classpath)
            names.append(_cell(row, "Product Name"))

        if not texts:
            return cls()

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:  # pragma: no cover - dependency is pinned
            return cls()

        vectorizer = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True
        )
        matrix = vectorizer.fit_transform(texts)
        instance = cls(classpaths=classpaths, product_names=names)
        instance._vectorizer = vectorizer
        instance._matrix = matrix
        return instance

    # -- lookup -------------------------------------------------------------
    def neighbours(self, text: str, k: int = 8) -> list[Neighbour]:
        """The `k` most similar labelled rows, most similar first."""
        if not self.fitted or not text:
            return []

        vector = self._vectorizer.transform([text.casefold()])
        scores = (self._matrix @ vector.T).toarray().ravel()
        if not scores.size:
            return []

        top = scores.argsort()[::-1][:k]
        return [
            Neighbour(
                classpath=self.classpaths[i],
                product_name=self.product_names[i],
                score=float(scores[i]),
            )
            for i in top
            if scores[i] > 0
        ]

    def rank(
        self, text: str, candidates: list[str], k: int = 12
    ) -> list[tuple[str, float]]:
        """Score each candidate classpath by neighbour vote, best first.

        Only candidates are scored, so retrieval can reorder the shortlist but
        never escape the approved taxonomy.
        """
        allowed = set(candidates)
        votes: dict[str, float] = {}
        for neighbour in self.neighbours(text, k=k):
            if neighbour.classpath in allowed:
                votes[neighbour.classpath] = votes.get(neighbour.classpath, 0.0) + neighbour.score
        return sorted(votes.items(), key=lambda kv: -kv[1])

    def decide(
        self, text: str, candidates: list[str], floor: float = 0.75, k: int = 5
    ) -> Neighbour | None:
        """A near-duplicate labelled row, or nothing.

        Summed neighbour votes are fine for ordering a shortlist but a poor
        basis for overriding the model: three mediocre matches outvote one good
        one. So a decision requires a single genuinely similar row whose nearest
        rival does not disagree.
        """
        allowed = set(candidates)
        found = [n for n in self.neighbours(text, k=k) if n.classpath in allowed]
        if not found:
            return None

        best = found[0]
        if best.score < floor:
            return None
        rival = next((n for n in found[1:] if n.classpath != best.classpath), None)
        if rival and rival.score > best.score * 0.95:
            return None  # two equally similar rows disagree; let the model choose
        return best

    def product_name_for(self, text: str, classpath: str, k: int = 8) -> str:
        """Modal product name among neighbours that share the chosen classpath."""
        votes: Counter = Counter()
        for neighbour in self.neighbours(text, k=k):
            if neighbour.classpath == classpath and neighbour.product_name:
                votes[neighbour.product_name] += neighbour.score
        return votes.most_common(1)[0][0] if votes else ""

    def summary(self) -> dict[str, int]:
        return {"indexed_rows": len(self.classpaths)}
