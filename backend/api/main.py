"""FastAPI service exposing the enrichment pipeline.

Endpoints fall into three groups:

* **Demonstration** - `/api/sample`, `/api/enrich` run a handful of rows
  synchronously so the UI can show one product's full provenance immediately.
* **Batch** - `/api/jobs` accepts an uploaded workbook or the bundled dataset,
  runs it on a worker thread and reports progress for polling.
* **Evidence** - `/api/evaluation` and `/api/knowledge` expose the holdout
  scores and the learned registries, so the numbers on screen can be checked
  against the code that produced them.

Security note: this service has no authentication and is bound to localhost by
default. It is a local demonstration and review tool. Exposing it on a public
interface would let anyone submit work and read every result, so an auth layer
would be required before doing that.
"""

from __future__ import annotations

import io
import json
import logging
import queue
import threading
from typing import Any

import pandas as pd
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.api.jobs import JobStore
from backend.config import PROJECT_ROOT, settings
from backend.core.schema import ProductRecord, delivery_columns
from backend.evaluation.scorer import Evaluator
from backend.knowledge.corrections import CORE_FIELDS, get_corrections
from backend.knowledge.datasets import SplitData, load_split, records_from, to_record
from backend.knowledge.registry import KnowledgeBase
from backend.pipeline.orchestrator import STAGES, EnrichmentPipeline, PipelineResult

logger = logging.getLogger(__name__)


# The built Vite bundle. Absent until `npm run build` has been run, in which
# case the API still serves and points the caller at the docs.
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(
    title="UniHack Product Intelligence",
    description="Multi-agent enrichment pipeline for industrial product data.",
    version="1.0.0",
)

# The UI is served from the same origin, but permitting localhost keeps a
# separately served frontend (e.g. a Vite dev server) working.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = JobStore()


class Context:
    """Lazily built pipeline state, shared across requests.

    The knowledge base is fitted on the *training* fold only, even though
    fitting on all 200 labelled rows would make the demo look better. Every
    score this service reports is therefore measured on rows the registries have
    never seen, which is the only version of the number worth quoting.
    """

    def __init__(self) -> None:
        # Reentrant: `pipeline` needs `kb`, which needs `split`, and each
        # accessor takes the lock. A plain Lock deadlocks on the first request.
        self._lock = threading.RLock()
        self._split: SplitData | None = None
        self._kb: KnowledgeBase | None = None
        self._pipeline: EnrichmentPipeline | None = None
        self._evaluation: dict[str, Any] | None = None
        # fold -> enriched records, kept so the review queue does not re-run
        # the pipeline on every poll. Invalidated by `refresh`.
        self._review_records: dict[str, dict[str, ProductRecord]] = {}

    @property
    def split(self) -> SplitData:
        with self._lock:
            if self._split is None:
                self._split = load_split()
            return self._split

    @property
    def kb(self) -> KnowledgeBase:
        with self._lock:
            if self._kb is None:
                self._kb = KnowledgeBase.fit(self.split.train_truth)
            return self._kb

    @property
    def pipeline(self) -> EnrichmentPipeline:
        with self._lock:
            if self._pipeline is None:
                self._pipeline = EnrichmentPipeline(self.kb)
            return self._pipeline

    def cached_evaluation(self) -> dict[str, Any] | None:
        return self._evaluation

    def store_evaluation(self, payload: dict[str, Any]) -> None:
        self._evaluation = payload

    def review_records(self, fold: str, refresh: bool = False) -> dict[str, ProductRecord]:
        """Enriched records for the review queue, keyed by part number.

        The first call runs the pipeline over the requested fold; later calls
        reuse the result. `refresh=True` re-runs it, which is also how reviewer
        corrections become visible in the queue (they replay as the final
        pipeline stage).
        """
        with self._lock:
            if not refresh and fold in self._review_records:
                return self._review_records[fold]
            split = self.split
            if fold == "train":
                frame = split.train_input
            elif fold == "all":
                frame = split.inputs
            else:
                fold, frame = "holdout", split.holdout_input
            result = self.pipeline.run(records_from(frame))
            records = {r.part_number: r for r in result.records}
            self._review_records[fold] = records
            return records


context = Context()


# --- helpers ---------------------------------------------------------------


def _record_payload(record: ProductRecord) -> dict[str, Any]:
    """A record shaped for the UI: input, output, and why."""
    return {
        "part_number": record.part_number,
        "sku": record.sku,
        "input": {
            "description": record.raw_description,
            "mpn": record.raw_mpn,
            "supplier": record.raw_manufacturer,
            "dept": record.dept,
            "class": record.klass,
            "fine": record.fine,
            "brand_hints": record.brand_hints,
        },
        "output": {
            "manufacturer_name": record.manufacturer_name,
            "brand_name": record.brand_name,
            "mpn": record.mpn,
            "classpath": record.classpath,
            "product_name": record.product_name,
            "series": record.series,
            "with_clause": record.with_clause,
            "invoice_desc": record.invoice_desc,
            "mobile_desc": record.mobile_desc,
            "short_desc": record.short_desc,
            "long_desc": record.long_desc,
            "retail_desc": record.retail_desc,
            "marketing_desc": record.marketing_desc,
            "mfr_url": record.mfr_url,
        },
        "attributes": [
            {
                "label": a.label,
                "value": a.value,
                "uom": a.uom,
                "confidence": a.confidence,
                "source": a.source,
            }
            for a in record.attributes
        ],
        "features": record.features,
        "approvals": record.approvals,
        "extras": record.extras,
        "confidence": record.confidence,
        "provenance": record.provenance,
        "issues": record.issues,
        "needs_review": record.needs_review,
        # Where the values came from: one entry per first-party document read,
        # and the per-value map of what was confirmed against which URL.
        "citations": record.citations,
        "grounded": record.grounded,
        "research_note": getattr(record.evidence, "note", "") or "",
    }


def _frame_from_upload(content: bytes, filename: str) -> pd.DataFrame:
    """Read an uploaded CSV or Excel file into a raw input frame."""
    buffer = io.BytesIO(content)
    lowered = filename.casefold()
    try:
        if lowered.endswith(".csv"):
            return pd.read_csv(buffer, dtype=str).fillna("")
        return pd.read_excel(buffer, dtype=str).fillna("")
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        raise HTTPException(400, f"could not read {filename}: {exc}") from exc


def _run_job(job_id: str, frame: pd.DataFrame, truth: pd.DataFrame | None) -> None:
    """Worker body: enrich the frame, score it when labels are available."""
    try:
        pipeline = context.pipeline

        def progress(done: int, _total: int, _record: ProductRecord) -> None:
            jobs.advance(job_id, done)

        result: PipelineResult = pipeline.run(records_from(frame), progress=progress)
        predicted = result.to_frame()

        metrics = None
        if truth is not None and not truth.empty:
            try:
                metrics = Evaluator().evaluate(predicted, truth, result.records)
            except Exception:  # noqa: BLE001 - scoring must not fail the job
                logger.exception("scoring failed for job %s", job_id)

        jobs.finish(
            job_id,
            done=len(result.records),
            rows=[_record_payload(r) for r in result.records],
            frame=predicted,
            summary=result.summary(),
            metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001 - report rather than crash the server
        logger.exception("job %s failed", job_id)
        jobs.fail(job_id, f"{type(exc).__name__}: {exc}")


def _start(frame: pd.DataFrame, label: str, truth: pd.DataFrame | None) -> dict[str, Any]:
    if frame.empty:
        raise HTTPException(400, "no rows to enrich")
    job = jobs.create(total=len(frame), label=label)
    thread = threading.Thread(
        target=_run_job, args=(job.id, frame, truth), daemon=True, name=f"job-{job.id}"
    )
    thread.start()
    return job.state()


# --- system ----------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Readiness plus what the knowledge base actually learned."""
    kb = context.kb
    split = context.split
    return {
        "status": "ok",
        "llm": {
            "providers": settings.provider_order,
            "configured": settings.has_api_key,
        },
        "dataset": {
            "labelled_rows": len(split.truth),
            "training_rows": len(split.train_truth),
            "holdout_rows": len(split.holdout_truth),
            "holdout_ratio": split.holdout_ratio,
        },
        "knowledge_base": kb.summary(),
        "corrections": get_corrections().summary(),
        "delivery_columns": len(delivery_columns()),
        "stages": list(STAGES),
    }


@app.get("/api/knowledge")
def knowledge() -> dict[str, Any]:
    """The learned registries, for inspecting how a decision was reached."""
    kb = context.kb
    return {
        "summary": kb.summary(),
        "classpaths": kb.taxonomy.all_classpaths,
        "templates": kb.taxonomy.templates,
        "brands": kb.manufacturers.brand_to_manufacturer,
        "mpn_prefixes": kb.manufacturers.mpn_prefix_brand,
        "description_tokens": kb.manufacturers.desc_token_brand,
        "sourcing_domains": kb.assets.brand_domain,
        "invoice_abbreviations": kb.style.abbreviations,
        "approvals": kb.approvals,
    }


@app.get("/api/style/{classpath:path}")
def style_for(classpath: str) -> dict[str, Any]:
    """The learned description grammar for one category."""
    kb = context.kb
    template = kb.taxonomy.template_for(classpath)
    if not template:
        raise HTTPException(404, f"no template for {classpath!r}")
    from backend.knowledge.style import SURFACES

    return {
        "classpath": classpath,
        "template": template,
        "surfaces": {
            surface: {
                "rows_learned_from": kb.style.style(classpath, surface).rows,
                "order": kb.style.build_order(classpath, surface, template),
                "suffixes": {
                    label: list(value)
                    for label, value in kb.style.style(classpath, surface).suffixes.items()
                },
            }
            for surface in SURFACES
        },
    }


# --- demonstration ---------------------------------------------------------


@app.get("/api/sample")
def sample(limit: int = 40, fold: str = "holdout") -> dict[str, Any]:
    """Raw rows from the bundled dataset, for the demo picker."""
    split = context.split
    frame = split.holdout_input if fold == "holdout" else split.train_input
    rows = frame.head(max(1, min(limit, 200))).to_dict(orient="records")
    return {
        "fold": fold,
        "count": len(rows),
        "rows": [{k: ("" if pd.isna(v) else str(v)) for k, v in row.items()} for row in rows],
    }


@app.post("/api/enrich")
def enrich(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Enrich up to a handful of rows synchronously, with full provenance.

    Accepts either `{"rows": [...]}` with raw input columns, or
    `{"part_numbers": [...]}` to pull those rows from the bundled dataset.
    """
    rows = payload.get("rows")
    part_numbers = payload.get("part_numbers")

    if part_numbers:
        frame = context.split.inputs
        selected = frame[frame["PART_NUMBER"].astype(str).isin([str(p) for p in part_numbers])]
        if selected.empty:
            raise HTTPException(404, "no matching part numbers in the bundled dataset")
        records = list(records_from(selected))
    elif rows:
        if len(rows) > 10:
            raise HTTPException(400, "use /api/jobs for more than 10 rows")
        records = [to_record(pd.Series(row)) for row in rows]
    else:
        raise HTTPException(400, "provide either rows or part_numbers")

    result = context.pipeline.run(records)
    truth = context.split.truth.set_index("PART_NUMBER")
    payloads = []
    for record, enriched in zip(records, result.records):
        item = _record_payload(enriched)
        key = record.part_number
        if key in truth.index:
            reference = truth.loc[key]
            if isinstance(reference, pd.DataFrame):
                reference = reference.iloc[0]
            item["truth"] = {
                column: ("" if pd.isna(reference.get(column)) else str(reference.get(column)))
                for column in (
                    "MANUFACTURER_NAME", "BRAND_NAME", "Classpath", "Product Name",
                    "INVOICE_DESC", "MOBILE_DESC", "SHORT_DESC", "RETAIL_DESC",
                    "LONG_DESC1",
                )
            }
        payloads.append(item)

    return {"summary": result.summary(), "records": payloads}


@app.post("/api/enrich/stream")
def enrich_stream(payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    """Enrich one row while streaming every stage transition as it happens.

    The UI watches the eight agents light up in real time: each stage emits a
    `start` event when its agent begins and an `end` event carrying the real
    elapsed milliseconds when it finishes. The final events carry the enriched
    record (with truth and provenance, same shape as `/api/enrich`) and the
    run summary, so the client needs exactly one request for the whole demo.

    Events are Server-Sent Events: `event: <type>` plus a JSON `data:` line.
    """
    part_numbers = payload.get("part_numbers") or []
    if len(part_numbers) != 1:
        raise HTTPException(400, "the stream endpoint enriches exactly one row")

    frame = context.split.inputs
    selected = frame[frame["PART_NUMBER"].astype(str).isin([str(p) for p in part_numbers])]
    if selected.empty:
        raise HTTPException(404, "no matching part number in the bundled dataset")
    record = next(iter(records_from(selected)))

    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def stage_hook(stage: str, event: str, rec: ProductRecord, elapsed_ms: float) -> None:
        events.put(
            {
                "type": "stage",
                "stage": stage,
                "event": event,
                "elapsed_ms": round(elapsed_ms, 1),
                "part_number": rec.part_number,
            }
        )

    def worker() -> None:
        try:
            result = context.pipeline.run([record], stage_hook=stage_hook, max_workers=1)
            enriched = result.records[0]
            item = _record_payload(enriched)

            truth = context.split.truth.set_index("PART_NUMBER")
            key = record.part_number
            if key in truth.index:
                reference = truth.loc[key]
                if isinstance(reference, pd.DataFrame):
                    reference = reference.iloc[0]
                item["truth"] = {
                    column: ("" if pd.isna(reference.get(column)) else str(reference.get(column)))
                    for column in (
                        "MANUFACTURER_NAME", "BRAND_NAME", "Classpath", "Product Name",
                        "INVOICE_DESC", "MOBILE_DESC", "SHORT_DESC", "RETAIL_DESC",
                        "LONG_DESC1",
                    )
                }

            events.put({"type": "record", "record": item})
            events.put({"type": "summary", "summary": result.summary()})
        except Exception as exc:  # noqa: BLE001 - report, never crash the stream
            logger.exception("stream enrichment failed")
            events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(None)  # sentinel: close the stream

    thread = threading.Thread(target=worker, daemon=True, name="enrich-stream")
    thread.start()

    def generate():
        while True:
            event = events.get()
            if event is None:
                break
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx must not buffer SSE
            "Connection": "keep-alive",
        },
    )


# --- batch -----------------------------------------------------------------


@app.post("/api/jobs/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Enrich an uploaded CSV or Excel file of raw catalogue rows."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty upload")
    frame = _frame_from_upload(content, file.filename or "upload.xlsx")
    if "PART_NUMBER" not in frame.columns:
        raise HTTPException(
            400, "input must contain a PART_NUMBER column; see /api/sample for the shape"
        )
    return _start(frame, label=file.filename or "upload", truth=None)


@app.post("/api/jobs/dataset")
def dataset_job(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Run the bundled dataset, scored against ground truth when labelled."""
    fold = str(payload.get("fold", "holdout"))
    limit = payload.get("limit")
    split = context.split

    if fold == "holdout":
        frame, truth = split.holdout_input, split.holdout_truth
    elif fold == "train":
        frame, truth = split.train_input, split.train_truth
    elif fold == "all":
        frame, truth = split.inputs, split.truth
    else:
        raise HTTPException(400, "fold must be holdout, train or all")

    if limit:
        frame = frame.head(int(limit))
    return _start(frame, label=f"dataset:{fold}", truth=truth)


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
def job_state(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job.state()


@app.get("/api/jobs/{job_id}/results")
def job_results(job_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.status != "done":
        raise HTTPException(409, f"job is {job.status}")
    window = job.rows[offset : offset + max(1, min(limit, 200))]
    return {
        "id": job.id,
        "total": len(job.rows),
        "offset": offset,
        "records": window,
        "summary": job.summary,
    }


@app.get("/api/jobs/{job_id}/metrics")
def job_metrics(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.metrics is None:
        raise HTTPException(404, "this job had no ground truth to score against")
    return job.metrics


@app.get("/api/jobs/{job_id}/export")
def job_export(job_id: str) -> StreamingResponse:
    """Download the enriched rows in the 252-column delivery format."""
    job = jobs.get(job_id)
    if not job or job.frame is None:
        raise HTTPException(404, "no exportable result for this job")

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        job.frame.to_excel(writer, sheet_name="Delivery Format", index=False)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="delivery-{job.id}.xlsx"'
        },
    )


# --- evidence --------------------------------------------------------------


@app.get("/api/evaluation")
def evaluation(refresh: bool = False) -> dict[str, Any]:
    """Holdout scores. Cached, because a full run costs API calls."""
    if not refresh and (cached := context.cached_evaluation()):
        return cached

    split = context.split
    result = context.pipeline.run(records_from(split.holdout_input))
    payload = {
        "fold": "holdout",
        "rows": len(result.records),
        "pipeline": result.summary(),
        "metrics": Evaluator().evaluate(
            result.to_frame(), split.holdout_truth, result.records
        ),
    }
    context.store_evaluation(payload)
    return payload


# --- review ----------------------------------------------------------------


def _truth_row(part_number: str) -> dict[str, str]:
    """Ground-truth values for one part number, when the dataset labels it."""
    truth = context.split.truth.set_index("PART_NUMBER")
    if part_number not in truth.index:
        return {}
    reference = truth.loc[part_number]
    if isinstance(reference, pd.DataFrame):
        reference = reference.iloc[0]
    columns = (
        "MANUFACTURER_NAME", "BRAND_NAME", "Classpath", "Product Name",
        "INVOICE_DESC", "MOBILE_DESC", "SHORT_DESC", "RETAIL_DESC",
        "LONG_DESC1", "MARKETING_DESCRIPTION", "UPC", "GTIN",
    )
    return {
        column: ("" if pd.isna(reference.get(column)) else str(reference.get(column)))
        for column in columns
    }


@app.get("/api/review/summary")
def review_summary() -> dict[str, Any]:
    """How many rows are waiting on a reviewer, and how many were decided."""
    store = get_corrections()
    return {"corrections": store.summary(), "reviewable_fields": list(CORE_FIELDS)}


@app.get("/api/review/queue")
def review_queue(
    fold: str = "holdout",
    status: str = "pending",
    limit: int = 100,
    refresh: bool = False,
) -> dict[str, Any]:
    """The review queue: one line per record, most urgent first.

    `status` filters on the review state: `pending` (default), `approved`,
    `corrected`, or `all`. Ordering puts flagged, low-confidence rows first,
    because those are the ones a reviewer should look at before anything else.
    """
    if status not in ("pending", "approved", "corrected", "all"):
        raise HTTPException(400, "status must be pending, approved, corrected or all")
    records = context.review_records(fold, refresh=refresh)
    store = get_corrections()

    rows: list[dict[str, Any]] = []
    for part_number, record in records.items():
        state = store.status_of(part_number)
        if status != "all" and state != status:
            continue
        rows.append(
            {
                "part_number": part_number,
                "brand": record.brand_name or "",
                "product_name": record.product_name or "",
                "description": record.raw_description,
                "needs_review": record.needs_review,
                "confidence": round(record.confidence.get("overall", 0.0), 3),
                "issues": record.issues,
                "status": state,
                "sourced": bool(record.citations),
            }
        )

    # Flagged rows first, then lowest confidence, then part number for stability.
    rows.sort(key=lambda r: (not r["needs_review"], r["confidence"], r["part_number"]))
    return {
        "fold": fold,
        "status": status,
        "total": len(rows),
        "rows": rows[: max(1, min(limit, 200))],
    }


@app.get("/api/review/{part_number}")
def review_record(part_number: str, fold: str = "holdout") -> dict[str, Any]:
    """One record for review: suggested values, evidence, and reference values."""
    records = context.review_records(fold)
    record = records.get(part_number)
    if record is None:
        raise HTTPException(404, f"no record {part_number!r} in fold {fold!r}")
    payload = _record_payload(record)
    payload["truth"] = _truth_row(part_number)
    payload["review_status"] = get_corrections().status_of(part_number)
    payload["decision"] = get_corrections().get(part_number)
    return payload


@app.post("/api/review/{part_number}/decision")
def review_decision(part_number: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Accept or override a record's values; the decision feeds future runs.

    The body carries `status` (`approved` or `corrected`) plus any of `fields`,
    `attributes`, `extras` and `notes`. Only the values the reviewer touched
    need to be present. The decision is persisted and replayed as the final
    pipeline stage on every subsequent run, so the correction never regresses.
    """
    status = str(payload.get("status", "")).strip()
    if status not in ("approved", "corrected"):
        raise HTTPException(400, "status must be 'approved' or 'corrected'")

    fields = payload.get("fields") or {}
    attributes = payload.get("attributes") or []
    extras = payload.get("extras") or {}
    if status == "corrected" and not (fields or attributes or extras):
        raise HTTPException(400, "a corrected decision needs at least one override")

    decision = get_corrections().record(
        part_number,
        status=status,
        fields=fields,
        attributes=attributes,
        extras=extras,
        notes=str(payload.get("notes", "")),
    )

    # Replay onto the cached record so the queue reflects the decision
    # immediately, without waiting for the next full run.
    for fold_records in context._review_records.values():
        if (record := fold_records.get(part_number)) is not None:
            get_corrections().apply(record)

    return {"part_number": part_number, "decision": decision}


# --- frontend --------------------------------------------------------------

if (FRONTEND_DIST / "index.html").exists():
    # Hashed asset filenames, so they are safe to cache aggressively; the HTML
    # entry point is served separately below and must not be cached.
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

else:  # pragma: no cover - only before the frontend has been built

    @app.get("/", include_in_schema=False)
    def index() -> JSONResponse:
        return JSONResponse(
            {
                "detail": "Frontend not built.",
                "fix": "cd frontend && npm install && npm run build",
                "api_docs": "/docs",
            },
            status_code=503,
        )
