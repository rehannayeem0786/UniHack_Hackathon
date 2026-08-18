"""Scoring the traceability/sourcing block, including the third-party fallback.

`score_sourcing` is what turns retrieved evidence into a reach number. The
third-party fallback adds two new measures — how many documents came from the
reputable allowlist and how many rows it actually supplemented — and they are
asserted here so a regression can't silently drop the fallback's contribution
from the metrics the brief is judged on.
"""

from __future__ import annotations

from backend.core.schema import ProductRecord
from backend.evaluation.scorer import Evaluator


def rec(citations, grounded=None, mfr_url=""):
    return ProductRecord(
        part_number="1",
        citations=citations,
        grounded=grounded or {},
        mfr_url=mfr_url or None,
    )


def citation(url, source, kind="specification"):
    return {"url": url, "kind": kind, "source": source}


class TestScoreSourcingThirdParty:
    def test_first_party_citations_are_not_counted_as_fallback(self):
        recs = [
            rec([citation("https://www.dewalt.com/spec.pdf", "first-party")]),
            rec([citation("https://www.dewalt.com/p/x", "first-party", "product-page")]),
        ]
        block = Evaluator().score_sourcing(recs)
        assert block["third_party_documents"] == 0
        assert block["records_supplemented_third_party"] == 0

    def test_third_party_citations_are_counted_per_document_and_record(self):
        recs = [
            rec(
                [
                    citation("https://www.gs1.org/products/1", "third-party"),
                    citation("https://www.nema.org/x", "third-party"),
                ]
            ),
            rec([citation("https://www.gs1.org/products/2", "third-party")]),
        ]
        block = Evaluator().score_sourcing(recs)
        assert block["third_party_documents"] == 3
        assert block["records_supplemented_third_party"] == 2

    def test_mixed_record_counts_both_families(self):
        recs = [
            rec(
                [
                    citation("https://www.dewalt.com/spec.pdf", "first-party"),
                    citation("https://www.gs1.org/products/1", "third-party"),
                ]
            )
        ]
        block = Evaluator().score_sourcing(recs)
        assert block["documents_read"] == 2
        assert block["third_party_documents"] == 1
        assert block["records_supplemented_third_party"] == 1
        assert block["records_with_a_source"] == 1

    def test_empty_records_do_not_divide_by_zero(self):
        block = Evaluator().score_sourcing([])
        assert block["records"] == 0
        assert block["sourced_rate"] == 0.0
        assert block["third_party_documents"] == 0
        assert block["records_supplemented_third_party"] == 0

    def test_third_party_is_still_a_verifiable_source(self):
        # A supplement is a source too: it must count toward retrieval reach so
        # the fallback's improvement is visible, not hidden from the headline.
        recs = [rec([citation("https://www.gs1.org/products/1", "third-party")])]
        block = Evaluator().score_sourcing(recs)
        assert block["records_with_a_source"] == 1
        assert block["sourced_rate"] == 1.0
