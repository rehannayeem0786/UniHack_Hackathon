# UniHack 2026 — Submission Deck Content

Paste-ready content for the official template, slide by slide. Every figure here
is measured on the 66-row holdout fold and reproducible with
`python scripts/run_pipeline.py --fold holdout`.

---

## Slide 2 — Team Details

```
a. Team name:        Unilog Product Intelligence
b. Team leader name: Rehan
```

---

## Deliverables in this repository

| Deliverable | Where | Produced by |
|---|---|---|
| Working software (code + architecture) | `backend/`, `frontend/`, `scripts/` | — |
| Enriched dataset, all 200 rows, exact delivery format | `runs/delivery-all-*.xlsx` (newest) | `python scripts/run_pipeline.py --fold all --export` |
| Evaluation report (accuracy, coverage, compliance) | `docs/Evaluation_Report.pdf` | `python scripts/build_report.py` |
| Submission deck (official template, screenshots embedded) | `docs/UniHack_2026_Unilog_Product_Intelligence.pptx` | `python scripts/build_deck.py` |
| Dashboard screenshots | `docs/shots/*.png` | `node frontend/shots.mjs` (service on :8000) |
| Schema compliance gate | `python scripts/verify_schema.py` | asserts 252/252 headers, exits non-zero on drift |
| Test suite | `python -m pytest tests -q` | 182 tests, no network, no API key |

Everything above is reproducible from a clean checkout with an API key for
Groq or Gemini.

---

## Slide 3 — Brief about your solution

**Unilog Product Intelligence — a multi-agent enrichment pipeline that turns one
cryptic catalogue line into a complete 252-column commerce-ready product record,
and shows its working for every field.**

A distributor hands over this:

```
PDSH4816AF Dishwasher SS - Display Only
Part_Manuf: "Appliance Dealers Cooperative (APPDE)"     ← a buying co-op, not a manufacturer
E1_Brand: "-- Unbranded --"   Unilog_Brand: "-- No Unilog Brand --"   ← placeholders, not data
```

The delivery format wants 252 columns: the resolved brand *and* its parent
manufacturer with `®`/`™` exact, a taxonomy classpath, an ordered attribute grid
drawn from a controlled vocabulary, the same product rewritten **five times** to
five different length and casing rules, plus digital assets and a compliant
sourcing URL.

Eight stages close that gap. Three things make it different from a
prompt-and-hope approach:

1. **The house style is learned, not hardcoded.** The five description surfaces
   follow a per-category grammar. We *mine* that grammar from labelled rows
   instead of writing 62 categories of rules by hand.
2. **Generation is structurally constrained.** Descriptions are assembled by
   formula from attributes that have already passed validation, so a fluent
   sentence containing an invented specification is impossible rather than
   filtered out afterwards.
3. **The evaluation is built to be hard to fool.** Registries are fitted on a
   134-row training fold and every number we quote is measured on the 66 rows
   they never saw.

---

## Slide 4 — The three questions

### 1. How does your solution enrich minimal product information?

Eight stages, knowledge base first and the model only where it adds signal.
**Four of the eight never call a model at all.**

| # | Stage | What it does | Model's role |
|---|---|---|---|
| 1 | **Classify** | Category triple narrows 62 classpaths to a shortlist. A near-duplicate labelled row (TF-IDF, char n-grams) decides it outright. | Picks from a list |
| 2 | **Resolve brand** | Brand hint → part-number prefix → description shorthand. Co-op supplier strings are ignored. | Last resort only |
| 3 | **Research** | Locates the manufacturer's own product page for the part number (never a marketplace), reads its spec table / JSON-LD, and grounds values verbatim against the URL. | **None** |
| 4 | **Extract attributes** | The classpath template fixes which attributes exist and their order; answers are snapped onto the controlled vocabulary. | Reads values |
| 5 | **Build descriptions** | Five surfaces assembled by the mined grammar from validated attributes. | None for the five scored surfaces (formula); one constrained call for marketing prose |
| 6 | **Assets & sourcing** | Asset filenames from the house naming convention, approved manufacturer domain, dimensions split into columns. | **None** |
| 7 | **Validate & score** | Character limits, casing, unit spacing, fractions, vocabulary membership. Per-field confidence + review flag. | **None** |
| 8 | **Corrections** | Replays reviewer decisions from the human-in-the-loop queue, so an accepted override is final and never regresses. | **None** |

**Worked example — a real held-out row, verbatim output.** Part `25284794`, a row
the knowledge base was never fitted on:

```
IN   Part_Desc   "R12AGTR10KW 15A GFCI Outler Wh"        ← note the typo "Outler"
     Part_Manuf  "Leviton Mfg Co (4927)"
     Category    Electrical > Wire Devices > Receptacles

OUT  Brand       Leviton®                    ✓ matches reference
     Manufacturer Leviton                    ✓ matches reference
     Classpath   Electrical>Wiring Devices>GFCI & AFCI Devices>GFCI & AFCI Receptacles
                                             ✓ matches reference
     Attributes  27 of 30 template labels filled
     Sourcing    https://leviton.com         (manufacturer's own site)
     Assets      Leviton_R12_AGTR1_0KW.jpg + 4 alternates + spec sheet,
                 install manual, catalogue, line drawing;  Warranty: 2 Year
     Confidence  0.924 — no human review required

     Invoice (11)  OUTLET GFCI
     Mobile  (61)  Leviton, GFCI Outlet, R12-AGTR1-0KW, 125 V, 15 A, 60 Hz, 0 hp
     Search  (66)  Leviton® R12-AGTR1-0KW GFCI Outlet, 125 V, 15 A, White, NEMA 5-15R
     Retail  (43)  GFCI Outlet, 125 V, 15 A, White, NEMA 5-15R
     Product page (362)
       Leviton® GFCI Outlet, 125 V, 15 A, 60 Hz, 0 hp, 10 kA Interrupt,
       10 kA Short Circuit, 2 Poles, 3 Wires, 14 to 10 AWG Wire,
       Self-Grounding Grounding, IP20, NEMA 5-15R, IP54, 1250 V AC Dielectric
       Strength, LED Indicator, Wall, Back Wired Terminal, Back Wired,
       Thermoplastic Body, Brass Contact, White, -35 to 66 deg C,
       1.68 in Length, 1.31 in Width, 4.19 in Height
```

Every rule visible here was **learned, not written**: `2 Poles` and `3 Wires`
append their label; `125 V` drops it; `1.68 in Length` appends it and keeps the
decimal because 1.68 is not a valid trade fraction; the invoice line reverses
`GFCI Outlet` to `OUTLET GFCI` because that is how the reference writes till
receipts. In another category the same machinery produces `Duplex Receptacle
Cover`, `Square Box`, and `4 in Length` on the product page but `4 in L` on the
retail label.

### 2. How does your solution ensure accuracy and trust?

Six layers, in the order they apply:

**a. Constrain before you generate.** The model never invents a classpath — it
chooses from a shortlist. Attribute values are snapped onto the observed
vocabulary. Brand and manufacturer names are snapped onto approved spellings so
symbols and legal suffixes match exactly. Off-list answers are recorded as
`llm-offlov` rather than silently accepted.

**b. Formula assembly, not free prose.** Four of the five descriptions contain
only values that already passed the vocabulary and UOM checks. Fabrication is
structurally impossible, not filtered. The one free-prose field
(`MARKETING_DESCRIPTION`) is given the validated attribute set and told to use
nothing else.

**c. First-party verification.** Where a value comes from the web, it comes from
the manufacturer's own site only — the domain policy fails closed, marketplaces
and distributors are refused even on redirect, and every retrieved value is
checked for **verbatim presence** in the fetched document before it earns a
citation. 88 attribute values (26% of filled values) carry a URL a reviewer can
open. `UPC`/`GTIN` are written only when the check digit validates.

**d. Deterministic rule validation.** Character limits, ALL-CAPS, unit spacing
(`24 in`, never `24in`), decimal→fraction (`50.25` → `50-1/4`), vocabulary
membership. Measured on the holdout:

| House rule | |
|---|---:|
| Invoice ≤ 40 characters | **100%** |
| Invoice ALL CAPS | **100%** |
| Mobile within 60–80 characters | **100%** |
| Unit spacing correct | **100%** |
| Fractions not decimals | **100%** |

**e. Per-field confidence and a review flag.** Every field carries a confidence
and a provenance string — `mpn-prefix:DR7`, `retrieval:0.81`, `llm+lov-snap:92`,
`brand-exact`, `spec-table:<url>`. A weighted score below threshold flags the
row: **12% of holdout rows (8 of 66) were routed to human review** rather than
passed off as confident.

**f. Report gaps instead of filling them.** `EAN`, `UNSPSC`, `TRADE_NAME` are
left blank because they cannot be derived. Where no first-party product page is
found, `MFR URL` stays the verified manufacturer domain rather than a guessed
deep link — a true statement rather than a plausible one.

**Result on 66 unseen rows:**

| Field (66 held-out rows) | Exact | Fuzzy | Similarity |
|---|---:|---:|---:|
| `MANUFACTURER_PART_NUMBER` | **100%** | 100% | 100% |
| `MANUFACTURER_NAME` | **89%** | 92% | 94% |
| `BRAND_NAME` | **89%** | 92% | 94% |
| `Classpath` | **74%** | 76% | 96% |
| `Product Name` | 47% | 73% | 88% |
| `LONG_DESC1` (product page) | 0% | 70% | **89%** |
| `SHORT_DESC` (search) | 0% | 61% | **88%** |
| `RETAIL_DESC` | 0% | 64% | 85% |
| **Mean across 10 scored fields** | **40.4%** | **72.7%** | — |

Plus: attribute label precision **93.2%** (1,397 labels emitted), attribute value
accuracy **59.7%**, schema coverage **71.1%** of what a human filled, and
**252/252 delivery headers matching the Expected Output sheet exactly** with
148 columns populated — asserted by `python scripts/verify_schema.py`, not
claimed.

> **We say this plainly:** description exact-match is near zero and that is the
> honest story of this dataset. The reference descriptions contain amperage,
> drum material and annual energy figures that appear **nowhere** in a 15-character
> input — they can only come from the manufacturer's own documentation. We
> reproduce the structure, ordering and house style correctly (88–89% similarity)
> and refuse to invent the content. A judge can verify this in one command:
> `python scripts/inspect_rows.py --limit 4 --diff`.

### 3. What makes it scalable for enterprise catalogs?

**Large catalogs.** Concurrent per-row workers; the scored 66-row run is 92 s
wall clock (1.4 s/row at 6 workers) including live first-party web retrieval,
and throughput scales linearly with worker count. Every stage is
failure-isolated — a broken row records an issue and continues rather than
aborting the batch. Responses are cached on disk by prompt hash, so re-runs are
free and deterministic (66 rows in 3.8 s). Four of eight stages and every
retrieval hit cost nothing; the live model bill for the fold was 11,327 tokens
(~170 per row), 47,360 worst case cold-cache — a fraction of a cent.

**Rate limits are survivable, not fatal.** During the first web-enabled run
**four models hit their rate limits** — `gemini-3.6-flash`, `llama-3.3-70b`,
`gpt-oss-20b`, `qwen3.6-27b`. The client classified each 429 as *exhausted*
rather than *retry*, dropped that model from the chain and continued down it.
Seven models across two providers served the batch and **zero rows failed**.

**New manufacturers.** Brand resolution is three learned indexes (approved brand
strings, part-number prefixes, description shorthand) plus an LLM fallback
snapped back onto the approved list. A new manufacturer needs *data*, not code:
add labelled rows and `KnowledgeBase.fit()` picks it up. Unknown brands resolve
via the model, are marked `llm-offlist`, and are flagged for review — never
silently accepted.

**Different document formats.** The pipeline takes a DataFrame, so CSV, Excel and
API payloads already work (the dashboard accepts uploads). Every reference-data
loader is isolated in `backend/knowledge/`, so dropping in the real
`UniCat_Manufacturer_and_Brand_List` or `Unicat_Lov` workbooks means replacing a
`fit()` method, not touching the pipeline.

**Continuous updates.** Registries are fitted, not authored — as editors approve
more rows, re-fitting improves classification, brand resolution, the description
grammar and the abbreviation lexicon with no code change. Confidence scores make
this measurable: rising confidence and a falling review rate show the system
learning. The hash-based train/holdout split keeps that measurement honest
release over release.

---

## Slide 5 — Opportunities

### a. How is it different from other approaches?

| The common approach | Ours |
|---|---|
| Prompt an LLM to "write a product description" | Descriptions **assembled by formula** from pre-validated attributes — invented specs are impossible, not filtered |
| Hardcode formatting rules per category | The per-category grammar is **mined from labelled rows** (310 category-surface styles, 91 abbreviations learned) |
| Trust the supplier field for brand | Supplier co-ops are **ignored**; brand comes from part-number prefixes and description shorthand (100% accurate where it fires) |
| An LLM call per field | Retrieval answers it free where a near-duplicate exists; 4 of 8 stages never call a model |
| Report accuracy on all labelled data | Registries fitted on 134 rows, **every number measured on 66 held-out rows** |
| Fill every cell to look complete | Underivable fields **left blank and reported**; 12% of rows flagged for review |

### b. How does it solve the problem statement?

The brief asks for enrichment, validation and **explainable** outputs, and says
depth beats breadth. We deliver the full pipeline — classification, brand
resolution, attribute extraction, five-surface description building, cleansing
and normalisation, digital assets — with explainability as a first-class output:
every field carries a confidence and a provenance string, and the *learned rules
themselves* are browsable in the UI. It writes the exact 252-column delivery
format, header-for-header.

### c. USP

**We learned the client's own house style out of their own data, and we can show
you the rules we inferred.**

Anyone can prompt an LLM to write product copy. Reproducing *Unilog's* format —
`Duplex Receptacle Cover` but `Square Box`, `4 in Length` on the product page yet
`4 in L` on the retail label, `COVER SURF INDL` with the noun first — requires
knowing rules nobody wrote down. We mined them, and the **Learned rules** tab
prints exactly what was inferred so it can be checked rather than trusted.

Second USP: **we tell you what we don't know.** Confidence scores, review flags
and deliberately blank columns. In a system feeding a live commerce catalogue,
a wrong spec is far more expensive than a blank one.

---

## Slide 6 — List of features

**Enrichment**
- 8-stage multi-agent pipeline over the full 252-column delivery format
- Taxonomy classification into 62 classpaths, constrained to approved values
- Brand + parent-manufacturer resolution with `®`/`™` and legal suffixes exact
- First-party web research: manufacturer's own product page located, spec table / JSON-LD read, values grounded verbatim against the URL
- Ordered attribute extraction against per-category templates and controlled vocabularies
- Five description surfaces per product, each to its own length/casing rule
- Marketing copy generation restricted to validated attributes
- `UPC` / `GTIN` filled only when the check digit validates

**Cleansing & normalisation**
- Placeholder detection (`-- Unbranded --` → empty, not data)
- ~90-entry UOM canonicalisation; unit spacing enforcement
- Decimal → fraction conversion to 64ths (`50.25` → `50-1/4`)
- Title casing with trade-acronym exceptions (`LED`, `GFCI`, `NPT`)
- Mojibake repair for double-encoded trademark symbols

**Learned knowledge (no hardcoding)**
- Description grammar mined per category and surface — 310 styles
- Invoice abbreviation lexicon mined from 40-character lines — 91 entries
- Part-number prefix → brand index — 85 entries, purity-filtered
- Description shorthand → brand index
- Approved manufacturer sourcing domains — 36 brands
- Asset naming conventions, learned per brand

**Trust & explainability**
- Per-field confidence + weighted overall score
- Provenance string on every decision
- Automatic "needs human review" flag
- Human-in-the-loop review queue: accept or override any value; decisions replay as the final pipeline stage and never regress
- Deliberate blanks for underivable fields, reported separately
- Sourcing-hierarchy compliance (manufacturer site only, fail-closed)

**Platform**
- FastAPI service: single-row demo, batch jobs, progress, metrics, Excel export, review queue
- React + TypeScript dashboard: Enrich / Batch / Review / Evaluation / Learned rules
- 252-column Excel export in exact delivery order
- Upload your own CSV/Excel catalogue
- Multi-provider LLM chain (Groq + Gemini) with rate-limit failover and disk cache

**Evaluation**
- Hash-based train/holdout split, identical on every machine
- Field accuracy (exact + fuzzy), attribute agreement, rule compliance, schema coverage, traceability
- `verify_schema.py` — header compliance gate, exits non-zero on drift
- `npm run smoke` — headless browser test incl. WCAG contrast assertions

---

## Slide 7 — Process flow diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  RAW INPUT (11 columns)                                                 │
│  "PDSH4816AF Dishwasher SS - Display Only" · co-op supplier · placeholders│
└────────────────────────────────┬────────────────────────────────────────┘
                                 ▼
                    ┌────────────────────────┐
                    │  KNOWLEDGE BASE        │  fitted on the TRAINING fold only
                    │  · taxonomy + templates│  62 classpaths · 317 attr labels
                    │  · brand/mfr registry  │  85 MPN prefixes · 38 brands
                    │  · description grammar │  310 category-surface styles
                    │  · retrieval index     │  TF-IDF char n-grams
                    │  · assets + domains    │  36 sourcing domains
                    └───────────┬────────────┘
                                ▼
  ①  CLASSIFY ───────────────────────────────────────────► Classpath, Product Name
     triple → shortlist → near-duplicate decides, else model picks from list
                                ▼
  ②  RESOLVE BRAND ──────────────────────────────────────► Brand, Manufacturer, MPN
     hint → MPN prefix → shorthand → model (snapped to approved list)
                                ▼
  ③  RESEARCH ───────────────────────────────────────────► first-party facts + URLs
     manufacturer's own product page located, spec table / JSON-LD read,
     values grounded verbatim against the URL                  [no model]
                                ▼
  ④  EXTRACT ATTRIBUTES ─────────────────────────────────► ordered attribute grid
     template fixes order · values snapped to controlled vocabulary
                                ▼
  ⑤  BUILD DESCRIPTIONS ─────────────────────────────────► 5 surfaces + marketing
     learned grammar · only validated values can appear   [no model for the five]
                                ▼
  ⑥  ASSETS & SOURCING ─────────────────────────────────► URL, images, docs, dims
     naming convention · approved manufacturer domain            [no model]
                                ▼
  ⑦  VALIDATE & SCORE ──────────────────────────────────► confidence + review flag
     limits · casing · UOM · fractions · vocabulary               [no model]
                                ▼
  ⑧  CORRECTIONS ───────────────────────────────────────► reviewer overrides final
     replays accepted decisions from the review queue             [no model]
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  252-COLUMN DELIVERY RECORD  +  provenance  +  confidence               │
│         ├──► Excel export (exact header order)                          │
│         ├──► Dashboard (side-by-side, per-field provenance)             │
│         └──► Evaluation vs held-out ground truth                        │
└─────────────────────────────────────────────────────────────────────────┘
                                 ▼
                    low confidence ──► HUMAN REVIEW ──► decisions replayed by ⑧
                                                        (the loop that improves it)
```

---

## Slide 9 — Architecture diagram

```
┌──────────────────────── PRESENTATION ────────────────────────┐
│  React 18 · TypeScript · Tailwind · Radix (shadcn) · Recharts│
│  Enrich │ Batch │ Review │ Evaluation │ Learned rules        │
└───────────────────────────┬──────────────────────────────────┘
                            │  REST (same origin)
┌───────────────────────────▼──────────────────────────────────┐
│  API — FastAPI + Uvicorn                                     │
│  /api/enrich  /api/jobs/*  /api/review/*  /api/evaluation    │
│  /api/knowledge                                              │
│  in-process job registry · threaded workers · Excel streaming │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│  ORCHESTRATOR — 8 stages, ThreadPoolExecutor, per-stage       │
│  failure isolation, input order preserved                    │
│                                                              │
│   classifier → manufacturer → research → attributes          │
│        → descriptions → sourcing → validator → corrections   │
└──────┬──────────────────────┬────────────┬───────────────────┘
       │                      │            │
┌──────▼─────────────────────┐│  ┌─────────▼───────────────────┐
│  KNOWLEDGE (fit on train)  ││  │  LLM CLIENT                 │
│  TaxonomyRegistry          ││  │  provider chain:            │
│  ManufacturerRegistry      ││  │    Groq (5) → Gemini (5)    │
│  AttributeVocabulary       ││  │  429 → mark exhausted, fail │
│  DescriptionStyleRegistry  ││  │    over, never hard-fail    │
│  ClasspathRetriever(TFIDF) ││  │  SHA-256 disk cache         │
│  AssetRegistry             ││  │  usage + latency telemetry  │
│  CorrectionStore (JSONL)   ││  └─────────────────────────────┘
└──────┬─────────────────────┘│
       │              ┌───────▼────────────────────────────────┐
       │              │  SOURCING — first-party web retrieval  │
       │              │  policy (fail-closed) · discovery      │
       │              │  fetch (robots-aware, cached)          │
       │              │  extract (JSON-LD, spec tables, PDF)   │
       │              │  evidence (verbatim grounding)         │
       │              └────────────────────────────────────────┘
┌──────▼───────────────────────────────────────────────────────┐
│  CORE — 252-column schema contract · deterministic normalise │
│  EVALUATION — accuracy · compliance · coverage · traceability│
└──────────────────────────────────────────────────────────────┘

DATA:  Input 200 rows ──┬── hash split ──► TRAIN 134  ──► fits the knowledge base
                        └────────────────► HOLDOUT 66 ──► scores it (never fitted on)
```

---

## Slide 10 — Technologies used

| Layer | Technology |
|---|---|
| **AI / LLM** | Groq (Llama 3.3 70B, Llama 3.1 8B, GPT-OSS 20B/120B), Google Gemini (3.6 Flash, 3.5 Flash-Lite); JSON-schema-constrained generation; multi-provider failover chain |
| **ML / NLP** | scikit-learn TF-IDF (char n-gram) retrieval; RapidFuzz fuzzy matching (token-set, length-guarded); statistical rule mining for grammar + abbreviations |
| **Backend** | Python 3.11, FastAPI, Uvicorn, Pydantic v2 + pydantic-settings, pandas, openpyxl, XlsxWriter |
| **Frontend** | React 18, TypeScript, Vite 6, Tailwind CSS, Radix UI (shadcn/ui), Recharts, Framer Motion, Lucide |
| **Engineering** | ThreadPoolExecutor concurrency, SHA-256 disk cache, hash-based dataset splitting, Puppeteer headless smoke tests with WCAG contrast assertions |

**Prompt engineering:** every generative call is schema-constrained and given an
explicit allowed-value list; the model selects rather than composes wherever a
controlled vocabulary exists.

---

## Slide 11 — Estimated implementation cost (optional)

**Prototype: ₹0.** Built entirely on free tiers (Groq + Gemini) and open-source
libraries.

**Measured usage:** 161 model calls for 66 rows. On the scored run, 149 of them
were answered from the disk cache and the live bill was 11,327 tokens (~170 per
row); the worst-case cold-cache bill measured on the same fold is 47,360 tokens
(~720 per row). The table below uses the conservative cold-cache figure. The
five scored description surfaces are built by formula and cost nothing.

| Catalogue size | Tokens | Indicative cost* |
|---|---:|---:|
| 1,000 SKUs | 0.7 M | < ₹10 |
| 100,000 SKUs | 72 M | ~₹650 |
| 1,000,000 SKUs | 0.7 B | ~₹6,500 |

\* at ~$0.10 blended per million tokens for small hosted models; free tiers cover
the prototype entirely. Cost falls further with cache reuse across similar SKUs,
and four of eight stages plus every retrieval hit cost nothing.

**For context:** a human editor filling 252 columns for one SKU is 15–30 minutes.
At 100,000 SKUs that is 25,000+ hours. The pipeline processes a row in ~1.4
seconds and routes 12% to review — so the human effort left is on the rows that
actually need judgement.

---

## Slide 12 — Snapshots of the MVP

All six are already captured in `docs/shots/` by `node frontend/shots.mjs`
(with the service running on http://127.0.0.1:8000):

1. **Enrich — input vs output** → `03-input-vs-output.png`. Raw cryptic line +
   placeholders on the left, resolved identity with `®` intact on the right.
   The single most persuasive screenshot.
2. **Enrich — five description surfaces** → `04-surfaces.png`. All five cards
   with green character-limit badges, and one expanded against the human
   reference.
3. **Enrich — attributes + provenance** → `06-provenance.png`. Attribute grid
   with `source` and confidence meters, provenance panel showing
   `mpn-prefix:DR7` and a `spec-table:<url>` citation.
4. **Review queue** → `11-review.png`. A flagged row with the accept/override
   editor — the human-in-the-loop step whose decisions replay as the final
   pipeline stage.
5. **Evaluation — chart + scores** → `07-evaluation.png` + `08-chart.png`.
   Field accuracy chart, headline metrics, and the compliance list at 100%.
6. **Learned rules** → `09-learned-rules.png`. The mined grammar for Electrical
   Box Covers, showing `cover type → <value> Cover` and `length → <value> L`.
   This is the "we didn't hardcode it" proof.

Also worth including: the terminal output of `scripts/verify_schema.py` showing
**252/252 headers, PASS**.

---

## Slide 13 — Additional details / Future development

**Honest current limitations**
- Description exact-match is near zero: ~two thirds of reference specifications
  exist only on manufacturer websites, not in the input.
- Registries are fitted on 134 rows — 62 classpaths, 38 brands. Some surfaces are
  learned from a single example.
- First-party retrieval reaches 21% of rows (14 / 66): the ceiling is bot
  protection on manufacturer sites, not the pipeline. Where no product page is
  reachable, `MFR URL` stays the verified manufacturer domain rather than a
  guessed deep link.
- Buying-co-op rows remain the weak spot and are flagged for review.

**Next, in priority order**
1. **Vision-language extraction** from spec-sheet PDFs and label images for the
   dimension and weight columns currently blank — the retrieval layer already
   fetches those PDFs.
2. **Widen first-party reach** — official CDN/document-domain allow-lists and
   polite crawl scheduling to lift the 21% retrieval ceiling without ever
   touching a marketplace.
3. **Load the official reference pack** — the 27,000-row manufacturer master and
   161,000-row LOV would replace reconstructed registries wholesale. Loaders are
   already isolated for this.
4. **Close the learning loop at scale** — the review queue already replays
   decisions as the final stage; next, batch re-fit of the knowledge base from
   approved rows, with confidence trend as the progress metric.
5. **De-duplication across catalogues** — entity resolution on part number plus
   attribute fingerprint.
6. **Production hardening** — auth, a real job queue (Celery/Redis), and
   per-tenant knowledge bases.

---

## Slide 14 — Links

```
1. GitHub Public Repository:  <create repo, push, paste URL here>
2. Demo Video Link (3 min):   <record following the script below, paste URL>
3. Working Prototype Link:    <deploy, or note "run locally: python scripts/serve.py">
```

> The repository is initialised and committed (`.env`, `.venv/`, `node_modules/`,
> `.cache/`, `runs/` are git-ignored — verify with `git ls-files | grep .env`
> returning nothing before the first push). Remaining human steps, in order:
>
> 1. `gh repo create unilog-product-intelligence --public --source=. --push`
> 2. Record the 3-minute demo (script below; the service + dashboard are the props).
> 3. Paste both URLs into slide 13 of `docs/UniHack_2026_Unilog_Product_Intelligence.pptx`.

### Suggested 3-minute demo video script

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | The raw input row | "This is what a distributor sends: fifteen characters, a supplier who is actually a buying co-op, and three brand fields that are all placeholders. Unilog needs 252 columns from this." |
| 0:20–0:50 | Click Run, stages tick through | "Eight stages. Four of them never call a language model — the rules are deterministic. Watch the resolved brand: Frigidaire, with the registered symbol, from a co-op supplier string that said 'Unbranded'." |
| 0:50–1:30 | The five surfaces; expand one against the reference | "The same product, written five times to five different rules. Forty characters all-caps for the till receipt, sixty to eighty for mobile. Green badges mean the hard limits pass — a hundred percent of them do." |
| 1:30–2:00 | Attributes + provenance panel | "Every value came from a controlled vocabulary, and every field records how it was decided. This one says 'mpn-prefix DR7' — the part number told us the brand, not a guess. And where confidence is low, the row goes to the review queue — a human decision there replays as the final stage and never regresses." |
| 2:00–2:30 | Learned rules tab | "And this is the part we're proudest of. Nobody wrote these rules. We mined the client's house style out of their own labelled data: 'Duplex Receptacle Cover' but 'Square Box'; 'four inch Length' on the product page but 'four inch L' on the retail label." |
| 2:30–3:00 | Evaluation tab | "Measured on 66 rows the system was never trained on. Part numbers a hundred percent, brand and manufacturer eighty-nine, all house rules a hundred. Where the manufacturer's own page backs a value, it carries a URL you can open. And where we can't derive something — EAN, UNSPSC, trade name — we leave it blank and flag it. Twelve percent of rows go to a human. A wrong spec costs more than a blank one." |
