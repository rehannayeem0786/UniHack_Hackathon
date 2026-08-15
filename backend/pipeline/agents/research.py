"""Research: read the manufacturer's own page and documents for this part.

This is the stage that lets the pipeline say something it did not already know.
Every other agent works from the input row and from patterns mined out of the
labelled set; this one goes and reads the manufacturer's specification.

It runs after manufacturer resolution — it needs a brand to know whose site is
allowed — and before attribute extraction, which consumes the evidence.

The sequence per record:

1. Resolve the permitted domains: the domain learned from the labelled rows,
   plus the brand's known official domain. Nothing else is readable.
2. Discover candidate URLs on those domains.
3. Fetch candidates in order and **confirm the part number appears on the page**
   before accepting it. A product page for a different model is worse than no
   page, so an unconfirmed page is recorded as weak evidence at best and never
   becomes the `MFR URL`.
4. Follow the page's own PDF links, preferring specification sheets over
   manuals, until the document budget is spent.
5. Write `MFR URL` as the confirmed deep product link, fill `Ref URL 1-5` with
   the documents actually retrieved, and attach the evidence bundle.

Everything degrades cleanly. No key is needed, and with `WEB_ENABLED=false` or no
network the stage records a note, attaches an empty bundle, and the rest of the
pipeline behaves exactly as it did before retrieval existed.
"""

from __future__ import annotations

import logging
import re

from backend.config import settings
from backend.core.normalize import canonical_uom, clean, country_name, snap_inches
from backend.core.schema import ProductRecord
from backend.knowledge.registry import KnowledgeBase
from backend.sourcing import discovery
from backend.sourcing.evidence import Evidence, EvidenceBundle
from backend.sourcing.extract import classify_document, classify_page, mentions, valid_gtin
from backend.sourcing.fetch import WebFetcher, get_fetcher
from backend.sourcing.policy import domains_for

logger = logging.getLogger(__name__)

# Delivery columns that hold supporting source URLs.
REF_URL_COLUMNS: tuple[str, ...] = tuple(f"Ref URL {i}" for i in range(1, 6))

# Candidate pages we are willing to open before giving up on discovery.
# Raised from 4 after measurement: several brands (Speed Queen, Philips)
# publish product pages several results deep, and each extra attempt is cheap
# when the fetch cache is warm.
MAX_PAGE_ATTEMPTS = 6

# Document kinds worth spending a fetch on, best first.
_DOCUMENT_PRIORITY: tuple[str, ...] = ("specification", "manual")

# Retrieved specification label -> delivery column. Matched on the label, so a
# column is only written when the source names that exact property.
_EVIDENCE_COLUMNS: dict[str, str] = {
    "country of origin": "Country Of Origin",
    "assembled product height": "HEIGHT",
    "assembled product width": "WIDTH",
    "assembled product length": "LENGTH",
    "assembled product weight": "WEIGHT",
    "product height": "HEIGHT",
    "product width": "WIDTH",
    "product weight": "WEIGHT",
}

# Columns that carry a number plus a separate unit column.
_MEASURED_COLUMNS: frozenset[str] = frozenset({"HEIGHT", "WIDTH", "LENGTH", "WEIGHT"})

_MEASURE_RE = re.compile(r"([\d]+(?:\.\d+)?(?:[\s-]\d+/\d+)?|\d+/\d+)\s*-?\s*([A-Za-z\"']+)?")


def _split_measure(text: str) -> tuple[str, str]:
    """`8.5-in` -> `('8-1/2', 'in')`, `3.23-lbs` -> `('3.23', 'lb')`.

    Inches are converted to the fractional form the guidelines require; other
    units keep their decimal value. Returns empty strings when the text is not a
    measurement, so nothing is written on a failed parse.
    """
    match = _MEASURE_RE.search(clean(text))
    if not match:
        return "", ""
    raw, unit = match.group(1).strip(), canonical_uom(match.group(2) or "")
    if unit == "in":
        # `8.5` becomes `8-1/2` exactly; `5.6` snaps to the nearest sixteenth,
        # which is how a catalogue quotes it.
        raw = snap_inches(raw)
    return raw, unit


class ResearchAgent:
    """Retrieves and verifies first-party manufacturer sources for one record."""

    name = "research"

    def __init__(self, kb: KnowledgeBase, fetcher: WebFetcher | None = None) -> None:
        self.kb = kb
        self.fetcher = fetcher or get_fetcher()

    # -- entry point -------------------------------------------------------
    def run(self, record: ProductRecord) -> ProductRecord:
        bundle = EvidenceBundle()
        record.evidence = bundle

        mpn = (record.mpn or record.raw_mpn or "").strip()
        brand = record.brand_name or ""
        manufacturer = record.manufacturer_name or ""

        learned = self.kb.assets.domain_for(brand, manufacturer)
        permitted = domains_for(brand, manufacturer, learned)
        bundle.permitted_domains = sorted(permitted)

        if not mpn:
            bundle.note = "no part number to research"
            return record
        if not permitted:
            bundle.note = f"no approved manufacturer domain for {brand or manufacturer or 'unknown brand'}"
            return record

        pages = self._read_pages(record, mpn, permitted, bundle)
        self._read_documents(pages, mpn, permitted, bundle)

        self._record_urls(record, bundle)
        self._columns_from_evidence(record, bundle)
        self._barcodes_from_evidence(record, bundle)
        record.citations = bundle.citations()

        if not bundle.documents:
            bundle.note = bundle.note or "no first-party source found for this part"
        return record

    # -- pages -------------------------------------------------------------
    def _read_pages(
        self,
        record: ProductRecord,
        mpn: str,
        permitted: set[str],
        bundle: EvidenceBundle,
    ) -> list[tuple[Evidence, list[tuple[str, str]]]]:
        """Fetch candidate pages, keeping those that mention the part number."""
        hint = record.product_name or record.classpath or ""
        extra = hint.split(">")[-1].strip() if hint else ""
        try:
            urls = discovery.candidates(mpn, permitted, extra_terms=extra)
        except Exception as exc:  # noqa: BLE001 - discovery must never break a run
            logger.debug("discovery failed for %s: %s", mpn, exc)
            urls = []

        if not urls:
            bundle.note = "no candidate URL on the approved domain"
            return []

        accepted: list[tuple[Evidence, list[tuple[str, str]]]] = []
        confirmed = 0

        for url in urls[:MAX_PAGE_ATTEMPTS]:
            fetched = self.fetcher.document(url, permitted)
            if fetched is None or fetched.kind != "html":
                continue

            kind = classify_page(fetched.url, fetched.title)
            # A news, blog or catalogue page is on the right domain and is still
            # not a source for one product's specification: it names several
            # models, so a value read from it may belong to a different one.
            if kind == "editorial":
                logger.debug("skipping non-product page: %s", fetched.url)
                continue

            hit = (
                mentions(fetched.text, mpn)
                or mentions(fetched.title, mpn)
                or mentions(fetched.url, mpn)
                or any(mentions(v, mpn) for v in fetched.tables.values())
            )
            evidence = Evidence(
                url=fetched.url,
                kind=kind,
                title=fetched.title,
                text=fetched.text,
                tables=fetched.tables,
                from_cache=fetched.from_cache,
                mentions_mpn=hit,
                url_names_part=mentions(fetched.url, mpn),
            )
            # An unconfirmed page is not evidence about this part. Keep it out of
            # the bundle entirely rather than risk attributing another model's
            # specification to this record.
            if not hit:
                logger.debug("page did not mention %s: %s", mpn, fetched.url)
                continue

            bundle.add(evidence)
            accepted.append((evidence, fetched.links))
            confirmed += 1
            if confirmed >= 2:
                break

        if not accepted:
            bundle.note = "candidate pages did not mention this part number"
        return accepted

    # -- documents ---------------------------------------------------------
    def _read_documents(
        self,
        pages: list[tuple[Evidence, list[tuple[str, str]]]],
        mpn: str,
        permitted: set[str],
        bundle: EvidenceBundle,
    ) -> None:
        """Fetch the page's own PDFs, specification sheets first."""
        budget = settings.web_max_documents - len(bundle.documents)
        if budget <= 0 or not pages:
            return

        ranked: list[tuple[int, str, str]] = []
        seen: set[str] = set(bundle.urls)
        for _, links in pages:
            for url, anchor in links:
                if url in seen:
                    continue
                kind = classify_document(url, anchor)
                if kind not in _DOCUMENT_PRIORITY:
                    continue
                seen.add(url)
                ranked.append((_DOCUMENT_PRIORITY.index(kind), url, kind))

        # A document naming the part number outranks a generic family sheet.
        ranked.sort(key=lambda item: (item[0], 0 if mentions(item[1], mpn) else 1))

        for _, url, kind in ranked:
            if budget <= 0:
                return
            fetched = self.fetcher.document(url, permitted)
            if fetched is None or fetched.kind != "pdf" or not fetched.text.strip():
                continue
            bundle.add(
                Evidence(
                    url=fetched.url,
                    kind=kind,
                    title=fetched.title or url.rsplit("/", 1)[-1],
                    text=fetched.text,
                    from_cache=fetched.from_cache,
                    mentions_mpn=mentions(fetched.text, mpn) or mentions(url, mpn),
                )
            )
            budget -= 1

    # -- columns the input could never supply -------------------------------
    def _columns_from_evidence(
        self, record: ProductRecord, bundle: EvidenceBundle
    ) -> None:
        """Fill delivery columns whose values only exist on the source.

        Country of origin and packed dimensions cannot be derived from an
        abbreviated description by any amount of inference — they are facts held
        only by the manufacturer. Where a first-party specification names one,
        it is copied across with its citation. Nothing here is guessed: a column
        is written only on an exact label match against retrieved data.
        """
        for label, column in _EVIDENCE_COLUMNS.items():
            if column in record.extras:
                continue
            hit = bundle.table_lookup(label)
            if hit is None:
                continue
            raw, document = hit

            if column in _MEASURED_COLUMNS:
                value, uom = _split_measure(raw)
                if not value:
                    continue
                record.extras[column] = value
                record.extras[f"{column}_UOM"] = uom or "in"
            elif column == "Country Of Origin":
                # Sources publish `CN` as often as `China`; the format wants the name.
                record.extras[column] = country_name(raw)
            else:
                record.extras[column] = clean(raw)

            record.provenance[column] = f"first-party source: {document.url}"
            record.grounded[column] = document.url

    # -- barcodes ------------------------------------------------------------
    def _barcodes_from_evidence(
        self, record: ProductRecord, bundle: EvidenceBundle
    ) -> None:
        """Write validated UPC/GTIN codes into the delivery columns.

        The extraction stage only accepts codes that pass their own check
        digit, so nothing here is guessed: a barcode is copied only when a
        first-party source states it, and the citation is recorded so a
        reviewer can verify it. A 12-digit code is a UPC-A; the same code
        zero-padded to 14 digits is its GTIN, which is how the delivery
        format relates the two columns.
        """
        for label, column in (("UPC", "UPC"), ("GTIN", "GTIN")):
            if record.extras.get(column):
                continue
            hit = bundle.table_lookup(label)
            if hit is None:
                continue
            raw, document = hit
            code = re.sub(r"\D", "", raw)
            if not valid_gtin(code):
                continue
            record.extras[column] = code
            record.provenance[column] = f"first-party source: {document.url}"
            record.grounded[column] = document.url

        # Relate the columns: a UPC-A implies its GTIN-14 form.
        upc = record.extras.get("UPC", "")
        if upc and not record.extras.get("GTIN") and len(upc) == 12:
            gtin14 = f"00{upc}"
            if valid_gtin(gtin14):
                record.extras["GTIN"] = gtin14
                record.provenance["GTIN"] = "derived from UPC (zero-padded GTIN-14)"

    # -- delivery columns --------------------------------------------------
    def _record_urls(self, record: ProductRecord, bundle: EvidenceBundle) -> None:
        """Promote a verified deep link to `MFR URL`, documents to `Ref URL 1-5`."""
        page = bundle.best_product_page()
        if page is not None:
            record.mfr_url = page.url
            record.provenance["mfr_url"] = (
                f"verified product page, part number confirmed on page"
                if page.mentions_mpn
                else "manufacturer page, part number not confirmed"
            )
            record.confidence["mfr_url"] = 0.95 if page.mentions_mpn else 0.6

        references = [d.url for d in bundle.documents if d.url != record.mfr_url]
        for column, url in zip(REF_URL_COLUMNS, references):
            record.extras[column] = url
        if references:
            record.provenance["reference_urls"] = (
                f"{len(references)} first-party document(s) retrieved"
            )

        if bundle.documents:
            record.confidence["research"] = round(
                min(1.0, 0.45 + 0.2 * len(bundle.documents)), 3
            )
            record.provenance["research"] = ", ".join(
                sorted({d.kind for d in bundle.documents})
            )
