"""In-process job registry for enrichment runs.

Enriching a few hundred rows takes long enough that an HTTP request should not
wait for it, so each run becomes a job with a progress counter the UI polls.
The store is deliberately in memory: this is a single-process demonstration
service, and persisting jobs would mean a database that adds nothing to what is
being shown. A production deployment would swap this for a real queue, which is
why the pipeline itself knows nothing about jobs.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

MAX_JOBS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """One enrichment run and everything the UI needs to render it."""

    id: str
    total: int
    label: str = "upload"
    status: str = "queued"  # queued | running | done | failed
    done: int = 0
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None

    # Results, populated when the run finishes.
    rows: list[dict[str, Any]] = field(default_factory=list)
    frame: pd.DataFrame | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] | None = None

    @property
    def progress(self) -> float:
        return round(self.done / self.total, 3) if self.total else 0.0

    def state(self) -> dict[str, Any]:
        """The status payload, without the (potentially large) result rows."""
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "progress": self.progress,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "summary": self.summary,
            "has_metrics": self.metrics is not None,
        }


class JobStore:
    """Thread-safe job registry with a bounded history."""

    def __init__(self, limit: int = MAX_JOBS) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._limit = limit

    def create(self, total: int, label: str = "upload") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], total=total, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            # Drop the oldest finished jobs so a long session cannot grow
            # without bound.
            while len(self._order) > self._limit:
                stale = self._order.pop(0)
                self._jobs.pop(stale, None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [self._jobs[i] for i in reversed(self._order) if i in self._jobs]
        return [job.state() for job in jobs]

    def advance(self, job_id: str, done: int) -> None:
        with self._lock:
            if job := self._jobs.get(job_id):
                job.done = done
                job.status = "running"

    def finish(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in updates.items():
                setattr(job, key, value)
            job.status = "failed" if updates.get("error") else "done"
            job.finished_at = _now()

    def fail(self, job_id: str, error: str) -> None:
        self.finish(job_id, error=error)
