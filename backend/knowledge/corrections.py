"""Reviewer corrections: the human-in-the-loop memory.

The review queue lets a person accept or override any value the pipeline
produced. Each decision is appended to a JSONL store; the latest decision per
part number wins. On every subsequent run the pipeline replays those decisions
as its final stage, so a corrected value is never regenerated and never
regresses — the system learns from its reviewer instead of making the same
mistake twice.

Corrections are keyed by part number because that is the stable identity of a
catalogue row across re-runs and re-uploads. A decision stores only the fields
the reviewer touched; everything else stays whatever the pipeline produces, so
improvements to the agents still apply to reviewed rows.

The store is deliberately a flat append-only file: it is small, diffable, needs
no database, and survives a server restart, which is everything the loop needs.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import PROJECT_ROOT
from backend.core.schema import Attribute, ProductRecord

logger = logging.getLogger(__name__)

DEFAULT_PATH = PROJECT_ROOT / "data" / "corrections.jsonl"

# Reviewable core fields, keyed exactly as the API payload names them. Each
# maps onto the ProductRecord attribute of the same name.
CORE_FIELDS: tuple[str, ...] = (
    "manufacturer_name",
    "brand_name",
    "mpn",
    "classpath",
    "product_name",
    "series",
    "with_clause",
    "invoice_desc",
    "mobile_desc",
    "short_desc",
    "long_desc",
    "retail_desc",
    "marketing_desc",
    "mfr_url",
)

# Decision statuses. `approved` means the reviewer accepted the pipeline's
# output as-is; `corrected` means at least one value was overridden.
STATUSES: tuple[str, ...] = ("pending", "approved", "corrected")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_store: CorrectionStore | None = None
_store_lock = threading.Lock()


def get_corrections(path: Path | None = None) -> CorrectionStore:
    """The process-wide correction store, so every pipeline shares decisions."""
    global _store
    with _store_lock:
        if _store is None:
            _store = CorrectionStore(path)
        return _store


def reset_corrections() -> None:
    """Drop the shared store (tests use this to start clean)."""
    global _store
    with _store_lock:
        _store = None


class CorrectionStore:
    """Append-only reviewer decisions, replayed onto future pipeline runs."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self._lock = threading.Lock()
        # part_number -> latest decision payload
        self._decisions: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decision = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn line never loses the rest of the file
                    key = str(decision.get("part_number", ""))
                    if key:
                        self._decisions[key] = decision
        except OSError as exc:  # noqa: BLE001 - the loop degrades, not crashes
            logger.warning("could not read correction store %s: %s", self.path, exc)

    def _append(self, decision: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision, ensure_ascii=False) + "\n")

    # -- writing -------------------------------------------------------------
    def record(
        self,
        part_number: str,
        *,
        status: str,
        fields: dict[str, Any] | None = None,
        attributes: list[dict[str, Any]] | None = None,
        extras: dict[str, str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Persist one review decision and return the stored payload."""
        if status not in ("approved", "corrected"):
            raise ValueError(f"unknown decision status: {status!r}")
        decision = {
            "part_number": part_number,
            "decided_at": _now(),
            "status": status,
            "fields": {k: v for k, v in (fields or {}).items() if k in CORE_FIELDS},
            "attributes": [
                {
                    "label": str(a.get("label", "")).strip(),
                    "value": a.get("value"),
                    "uom": a.get("uom"),
                }
                for a in (attributes or [])
                if str(a.get("label", "")).strip()
            ],
            "extras": dict(extras or {}),
            "notes": notes.strip(),
        }
        with self._lock:
            self._decisions[part_number] = decision
            self._append(decision)
        return decision

    # -- reading -------------------------------------------------------------
    def get(self, part_number: str) -> dict[str, Any] | None:
        with self._lock:
            return self._decisions.get(part_number)

    def status_of(self, part_number: str) -> str:
        decision = self.get(part_number)
        return decision["status"] if decision else "pending"

    def summary(self) -> dict[str, int]:
        with self._lock:
            decisions = list(self._decisions.values())
        fields = sum(len(d.get("fields") or {}) for d in decisions)
        attributes = sum(len(d.get("attributes") or {}) for d in decisions)
        return {
            "decisions": len(decisions),
            "approved": sum(1 for d in decisions if d.get("status") == "approved"),
            "corrected": sum(1 for d in decisions if d.get("status") == "corrected"),
            "field_overrides": fields,
            "attribute_overrides": attributes,
        }

    # -- replay --------------------------------------------------------------
    def apply(self, record: ProductRecord) -> ProductRecord:
        """Overlay the latest reviewer decision onto an enriched record.

        Runs after the validator, so a corrected value is final: confidence is
        set to 1.0, provenance names the review, and the row is cleared for
        delivery. Fields the reviewer did not touch are left exactly as the
        pipeline produced them.
        """
        decision = self.get(record.part_number)
        if not decision:
            return record

        for key, value in (decision.get("fields") or {}).items():
            if key not in CORE_FIELDS:
                continue
            setattr(record, key, value if value else None)
            record.confidence[key] = 1.0
            record.provenance[key] = "reviewer-correction"

        overrides = {
            str(a.get("label", "")).strip().casefold(): a
            for a in (decision.get("attributes") or [])
        }
        if overrides:
            seen: set[str] = set()
            for attr in record.attributes:
                override = overrides.get(attr.label.casefold())
                if override is None:
                    continue
                seen.add(attr.label.casefold())
                value = override.get("value")
                attr.value = str(value) if value not in (None, "") else None
                uom = override.get("uom")
                attr.uom = str(uom) if uom else attr.uom
                attr.confidence = 1.0
                attr.source = "reviewer-correction"
                record.grounded.pop(attr.label, None)
            # A reviewer attribute that is not in the template is still appended:
            # the delivery format accepts up to fifty attribute rows.
            for folded, override in overrides.items():
                if folded in seen:
                    continue
                value = override.get("value")
                record.attributes.append(
                    Attribute(
                        label=str(override.get("label", "")).strip(),
                        value=str(value) if value not in (None, "") else None,
                        uom=str(override["uom"]) if override.get("uom") else None,
                        confidence=1.0,
                        source="reviewer-correction",
                    )
                )

        for column, value in (decision.get("extras") or {}).items():
            if value:
                record.extras[column] = str(value)
                record.provenance[column] = "reviewer-correction"

        # A reviewed row is a settled row.
        record.needs_review = False
        if decision.get("status") == "corrected":
            record.provenance["review"] = "values corrected by reviewer"
        else:
            record.provenance["review"] = "approved by reviewer as generated"
        return record

