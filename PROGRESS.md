# Build status — manufacturer source retrieval

> **Final state (2026-08-15).** Latest scored run:
> `runs/metrics-holdout-20260815-195642.json` — 66 holdout rows in **91.9 s
> (1.39 s/row)** with live first-party web retrieval; 161 model calls, 11,327
> live tokens (92.5% cache-hit rate), **0 failures**; 14/66 rows (21%) grounded
> against manufacturer product pages, 88 attribute values carrying URLs; 8/66
> rows (12%) routed to human review; mean confidence 0.712. Headline accuracy:
> part number **100%**, brand/manufacturer **89%** exact, all five house rules
> **100%**, 252/252 delivery headers verified. 157 tests pass. The numbers in
> the session log below are historical checkpoints, not the current state.

Session summary: what was added, what it measurably changed, and what to do next.

Everything below is measured on the **holdout fold** (66 of the 200 labelled rows,
assigned by hashing the part number). The knowledge base is fitted on the 134
training rows only, so the holdout numbers are not self-graded.

---

## 1. What the project could not do before

The pipeline was already complete end to end: six agents, a mined description
grammar, a 252-column delivery export that matches the organisers' header
byte for byte, an honest train/holdout split, and a metrics dashboard.

One thing was missing, and the README named it as the cause of the weakest
numbers: **nothing in the pipeline ever read a manufacturer's website.** Every
value came from the input row or from patterns mined out of the labelled set.
That capped three things structurally:

- `MFR URL` was a bare domain (`https://www.satco.com`), never a product page.
- `Ref URL 1-5` were always blank.
- Facts held only by the manufacturer — country of origin, packed dimensions —
  could not be produced at all.
- No output could be checked. A reviewer had to trust every value.

The brief asks for exactly this: *"enrichment from manufacturer sources"* is a
named pipeline step, *"validate and enrich information with traceable outputs"*
is a stated expected outcome, and the sourcing rule (manufacturer's own site
only; marketplaces and distributors excluded) is called out explicitly.

---

## 2. What was built

A new `backend/sourcing/` package plus a new pipeline stage, `research`, which
runs after manufacturer resolution (it needs a brand to know whose site it may
read) and before attribute extraction (which consumes what it finds).

| Module | Responsibility |
| --- | --- |
| `sourcing/policy.py` | Who may be read. Fail-closed. |
| `sourcing/discovery.py` | Locating a first-party URL for a part number. |
| `sourcing/fetch.py` | Polite, cached, robots-aware retrieval + extraction. |
| `sourcing/extract.py` | Page → specification pairs, prose, PDF links. |
| `sourcing/evidence.py` | Retrieved material + the citation that travels with it. |
| `pipeline/agents/research.py` | The stage that orchestrates the above. |

### The sourcing rule is enforced, not assumed

`policy.py` holds three lists — blocked marketplaces/distributors/aggregators,
brand → official domain, and first-party document CDNs — and one function,
`allowed()`, which **fails closed**: a domain that is neither the resolved
manufacturer domain nor a known official domain is refused even though it is on
nobody's block list. The check is applied twice, before the request and again on
the final URL after redirects, because a manufacturer that redirects to a
reseller has still landed us on a reseller. `tests/test_policy.py` asserts the
negative cases as firmly as the positive ones.

A search index is used **only to locate a URL**, never as a source of data:
queries are `site:`-restricted to an already-permitted domain, returned links are
re-checked against the policy, and the search engine's snippets are discarded.
The product data is then read from the manufacturer's own page.

### Values are verified, not just generated

Two mechanisms, and the distinction between them matters:

- **Spec-table lookup.** A manufacturer's own label/value grid is read directly.
  No inference at all — confidence 0.9, source recorded as `spec-table:<url>`.
- **Verbatim grounding.** Every filled attribute value is checked for literal
  presence in the retrieved text (case, spacing and punctuation ignored, so
  `50-1/4 in` matches `50 1/4"`). A value found word for word is promoted to
  0.97 and its source URL recorded in `record.grounded`. A value that is *not*
  found is left exactly as it was — absence from whatever we happened to fetch is
  weak evidence against a value — but it earns no promotion and no citation.

Values shorter than three characters are refused for grounding: a single digit
appears in every document and proves nothing.

### Everything degrades instead of failing

Same contract the LLM client already used. No API key is needed for retrieval.
With `WEB_ENABLED=false`, no network, a 403 bot wall, a timeout or a malformed
PDF, the stage records a human-readable note, attaches an empty evidence bundle,
and the pipeline behaves exactly as it did before retrieval existed. Anything
fetched previously still replays from `.cache/web/`.

---

## 3. Measured results

### New capability (was structurally impossible before)

| | Before | Now |
| --- | --- | --- |
| Verified deep product links in `MFR URL` | 0 / 66 | **14 / 66** |
| `Ref URL 1-2` populated with real documents | 0 / 66 | **6 / 66, 2 / 66** |
| `Country Of Origin` from a first-party source | 0 / 66 | **7 / 66** |
| `HEIGHT` / `WIDTH` / `LENGTH` / `WEIGHT` from source | 0 / 66 | **7 / 66** |
| Attribute values traceable to a URL, verbatim | 0 | **80** (24% of filled values) |
| Documents read (product pages + manuals) | 0 | 22 |
| Delivery columns populated | — | 148 / 252 |

Retrieval reach is **21% of rows (14 / 66)**, and that figure is reported rather
than smoothed over. See §5 for why, and why the honest answer is not to raise it
by force.

### Effect on the previously scored metrics

| Metric | Baseline | Retrieval v1 | **Retrieval v2 (current)** |
| --- | --- | --- | --- |
| Mean fuzzy match | 71.4% | 72.0% | **72.4%** |
| Mean exact match | 40.4% | 40.4% | **40.4%** |
| `INVOICE_DESC` fuzzy | 42.4% | 45.5% | **50.0%** |
| `MOBILE_DESC` fuzzy | 43.9% | 48.5% | **50.0%** |
| `RETAIL_DESC` fuzzy | 57.6% | 63.6% | **63.6%** |
| `SHORT_DESC` fuzzy | 71.2% | 62.1% | 59.1% |
| `LONG_DESC1` fuzzy | 75.8% | 66.7% | 68.2% |
| Attribute values compared | 338 | 232 | 280 |
| Attribute value accuracy | 62.1% | 57.3% | 60.7% |
| Schema fill vs truth | 73.1% | 69.4% | 70.8% |
| Rule: fractions not decimals | 97.0% | 86.4% | **100.0%** |

Identity fields are unchanged throughout: MPN 100%, manufacturer and brand 89%,
classpath 74%.

**Read this honestly: on the scored fields, retrieval is roughly a wash.** It
buys traceability, four newly-populated column groups and one rule that is now
perfect, at the cost of some attribute fill. It did not lift description
accuracy, because those fields are scored against a specific house phrasing that
a manufacturer's page does not supply.

### Two regressions found and fixed during the session

Both were caused by this work, found by measurement, and are now pinned by tests.

1. **Decimal inches leaked into descriptions** (fractions compliance 97% → 86%).
   Retrieved specs publish `5.6 in`; the existing `decimal_to_fraction` correctly
   refuses to invent precision and left the decimal, which breaks the house rule.
   Fixed with `snap_inches()`, which rounds a decimal *inch* to the nearest
   sixteenth — the catalogue convention, at most 1/32 in of rounding — and is
   applied only where the unit is known to be inches. Compliance is now **100%**,
   better than the original baseline.

2. **Prose context starved the attribute template** (values compared 338 → 232).
   Putting up to 6,000 characters of installation-manual text ahead of the
   attribute list made the model summarise the source instead of completing the
   template. Fixed by making the context **specification-pairs-first** and
   cutting the budget to 3,500 characters; pairs are compact, are the
   manufacturer's own label/value claims, and are never truncated away. Recovered
   280 / 338, and grounded values went *up* (75 → 80).

### Two pre-existing bugs fixed

- `MOJIBAKE` claimed to repair `â„¢` but the mapping used codepoint `U+0084`
  where CP1252 byte `0x84` actually decodes to `U+201E`, so the entry could never
  match real data. Trademark symbols in retrieved text now repair correctly.
- HTML was truncated to 240 KB *before* parsing. A manufacturer product page is
  routinely 1-3 MB of application shell wrapping 6 KB of product data, so the
  specification block was being cut off. The cache now stores the **extracted**
  document (title, prose, pairs, links) rather than raw markup: small, diffable,
  faithful on replay, and no longer lossy.

### Test suite

**142 tests, previously zero.** `pytest.ini` added; no test touches the network
(`test_research.py` drives the stage through a fake fetcher).

| File | Covers |
| --- | --- |
| `tests/test_policy.py` | Sourcing compliance, fail-closed behaviour |
| `tests/test_extract.py` | Spec tables, JSON-LD `@graph`, doc links, page kinds |
| `tests/test_evidence.py` | Grounding thresholds, ranking, prompt budget |
| `tests/test_research.py` | Verification, URL selection, degradation paths |
| `tests/test_normalize.py` | House-style rules incl. both regression cases |

---

## 4. Notable technical findings

Measured, not assumed — each one changed a design decision:

- **DuckDuckGo's HTML endpoints answer `202` to a non-browser client.** Mojeek,
  Startpage and Marginalia returned no on-site results for part-number queries.
  **Brave Search was the only index that honoured `site:`** and returned exact
  product URLs. It leads the endpoint list; the others remain as fallbacks.
- **Manufacturer sites are JavaScript applications.** The visible specification
  grid is hydrated client-side and is simply not in the HTML. The
  schema.org JSON-LD block those same sites publish for search engines carries
  `additionalProperty` — name/value pairs that map almost directly onto the
  delivery format's attribute triplets. That is now the primary extraction path;
  DOM tables are the fallback. DeWALT yields 22 real pairs per product this way.
- **A press release is the wrong source even on the right domain.** It names six
  other models, so an attribute read from it may belong to a different tool.
  Pages classified `editorial` are skipped, and `MFR URL` ranks on whether the
  URL *names the part* rather than on page length — before that fix, a Milwaukee
  press release beat the product's own page.
- **16 of 26 manufacturer domains are directly fetchable.** The rest answer 403
  (GE Appliances, Hager, Senco, Lithonia), 429 (Satco) or time out behind bot
  protection (Frigidaire, Whirlpool).

---

## 5. Known limitations

Stated plainly, because the brief rewards noticing gaps.

- **Retrieval reaches 21% of rows.** The ceiling is bot protection, not the
  code. Satco alone is 16 holdout rows and returns `429` to every request,
  politely paced or not; Philips, GE Appliances, Hager and Senco return `403`.
  These are deliberately **respected rather than worked around** — spoofing a
  browser to defeat a WAF would be the wrong call in a compliance-focused
  submission. Realistic reach with current access is 30-40%.
- **Descriptions still score 0% exact.** Retrieval supplies facts, not the house
  phrasing the ground truth uses. Fuzzy similarity is 77-88%.
- **`values_compared` is 280 vs a 338 baseline.** Partly the context change
  above, partly a confound: the first full run hit the Groq daily quota at row
  ~57 and fell back to weaker models, and those answers are now cached. Needs a
  clean re-run on fresh quota to separate the two.
- **Grounding is one-directional.** A value absent from the retrieved text is not
  demoted, only left unpromoted. Deliberate, but it means `grounded_rate` is a
  floor on traceability rather than a measure of correctness.
- **`Country Of Origin = CN` was being emitted** as a raw ISO code; now expanded
  via `country_name()`. Other sources may publish forms not yet in that map.
- All nine official reference workbooks are still absent from `data/`. The
  brand → official-domain list in `policy.py` stands in for
  `UniCat_Manufacturer_and_Brand_List.xlsx`; domains learned from the labelled
  rows take precedence over it.

---

## 6. What to do next

Ordered by value per hour of work.

### High value

1. **Clean re-run on fresh LLM quota.** Clear `.cache/` LLM entries (keep
   `.cache/web/`) and re-run the holdout on one strong model start to finish.
   This is the only way to settle whether the attribute-fill gap is the evidence
   context or the quota fallback, and it is the number a judge will ask about.
2. **Human-in-the-loop review queue.** The brief names it explicitly and it is
   the largest remaining gap. Confidence scores, `needs_review` flags and
   per-value citations all exist; the queue does not. Endpoints to list flagged
   records, accept/override a field, and — the part that would stand out — feed
   accepted corrections back into the knowledge base so the reviewer teaches the
   system. Add a Review tab alongside the existing four.
3. **Widen retrieval reach honestly.** Add official domains for uncovered brands
   (XO Appliance had none), and add per-domain URL templates for sites that are
   reachable but whose search coverage is thin (Trex, Edge Eyewear, Kichler,
   Speed Queen, Velux). Every candidate is still fetched and part-number-verified,
   so a wrong template costs nothing but a request.
4. **Git repository.** `.git` does not exist and `SUBMISSION.md` lists a public
   repo as a submission requirement. Confirm `.gitignore` covers `.env`,
   `.cache/`, `.venv/`, `node_modules/`, `runs/` before the first commit.

### Medium value

5. **Fill `UPC` / `GTIN` from JSON-LD.** The extractor already reads `gtin13` and
   `gtin12`; they are not yet written to the delivery columns. Values seen so far
   look float-rounded (`885912000000`), so validate the check digit before
   writing — a wrong barcode is worse than a blank one.
6. **Feed evidence into the description builder.** Marketing copy and feature
   bullets are the one place retrieved prose should help, and
   `MARKETING_DESCRIPTION` / `ITEM_FEATURES_*` currently do not see it.
7. **Extract spec tables from PDFs.** Manuals are fetched and read as flat text;
   their specification tables are not parsed. Highest-value remaining extraction
   work, since a manual is available even for brands whose product pages are
   walled.
8. **`SUBMISSION.md` placeholders**: team name, leader, repo URL, demo video,
   prototype link.

### Low value / cleanup

9. Refresh `docs/shots/` — screenshots predate the Retrieved-sources panel and
   the Traceability card. `docs/deck/` is empty.
10. Update `README.md` results tables with the v2 numbers and the new
    traceability section.
11. `settings.batch_size` is configured, documented and unused; either wire it up
    or delete it.
12. Retrieval adds ~7s per sourced row on a cold cache. If a live demo needs to
    be fast, pre-warm `.cache/web/` and say so rather than lowering the delays.

---

## 7. How to run

```bash
# Tests (no network, no API key)
.venv\Scripts\python.exe -m pytest tests -q

# Holdout run with delivery export + metrics JSON into runs/
$env:PYTHONPATH="."; .venv\Scripts\python.exe scripts\run_pipeline.py --fold holdout --export

# Confirm the export still matches the organisers' 252-column header exactly
$env:PYTHONPATH="."; .venv\Scripts\python.exe scripts\verify_schema.py

# Fully offline (cache replay only)
$env:WEB_ENABLED="false"; .venv\Scripts\python.exe scripts\run_pipeline.py --fold holdout

# UI
cd frontend; npm run build      # then serve the API and open it
```

New UI surfaces: a **Retrieved sources** panel per record (documents, what each
one confirmed verbatim, and a plain note when nothing was found) and a
**Traceability** card in the Evaluation tab (reach, grounded share, and fetcher
counters including requests blocked by policy and refused by `robots.txt`).
