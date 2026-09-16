"""
Purpose: Run long operations in the background and let a caller poll them.
Spec:    docs/implementation_plan_2026-09-15.md#2.3
Tests:   tests/web/test_jobs.py

A multi-source search takes minutes: eight sources, up to twenty pages each,
arXiv spaced three seconds apart, then per-record enrichment. A synchronous HTTP
request cannot hold that open, so the web app submits a job and polls it.

Deliberately in-process. For a handful of colleagues on one container, a job
registry over a thread pool is the right size; Redis or Celery would be more
moving parts than the problem has. The cost is that jobs do not survive a
redeploy — callers are told so by an explicit `expired` status rather than by a
poll that never returns.

Two rules this module enforces for every worker (learnings P15):
  * the whole body, setup included, runs inside try/except, and an escaping
    exception sets status "error" with the message. A worker may never exit
    leaving a job on "running", which is a spinner that turns forever.
  * a failure the worker *handled* but which lost data (a source that was
    unreachable) is recorded on the job, so "found nothing" and "half the
    sources were down" are not the same answer (P2).
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 4
DEFAULT_TTL_SECONDS = 3600


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL = (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED)


@dataclass
class Job:
    """One unit of background work and everything a poller needs to see."""

    id: str
    kind: str
    owner: str
    status: str = JobStatus.QUEUED
    phase: str = ""
    fetched: int = 0
    total: int = 0
    matched: int = 0
    error: str = ""
    result: Any = None
    # Sources that failed mid-run. The orchestrator swallows these per source;
    # surfacing them is what separates "no new papers" from "arXiv was down".
    sources_failed: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def should_stop(self) -> bool:
        """Passed to the orchestrator as its stop predicate."""
        return self._cancel.is_set()

    def to_dict(self) -> Dict[str, Any]:
        """The poll payload. Never includes the result itself, which is fetched
        separately and may be large."""
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "phase": self.phase,
            "fetched": self.fetched,
            "total": self.total,
            "matched": self.matched,
            "error": self.error,
            "sources_failed": list(self.sources_failed),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobRegistry:
    """Submit, poll, cancel and expire background jobs."""

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="biorx-job"
        )
        self.ttl_seconds = ttl_seconds

    # ── Submission ────────────────────────────────────────────────────────────

    def submit(self, kind: str, owner: str,
               work: Callable[[Job], Any],
               on_finish: Optional[Callable[[Job], None]] = None) -> Job:
        """Queue `work(job)` and return the Job immediately.

        `work` receives its own Job so it can report progress and check
        job.should_stop(). Its return value becomes job.result.
        """
        self._expire_old()
        job = Job(id=uuid.uuid4().hex, kind=kind, owner=owner)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, work, on_finish)
        return job

    def _run(self, job: Job, work: Callable[[Job], Any],
             on_finish: Optional[Callable[[Job], None]]) -> None:
        """The worker body. Everything is inside the guard, including setup."""
        try:
            if job.cancelled:
                job.status = JobStatus.CANCELLED
                return
            job.status = JobStatus.RUNNING
            result = work(job)
            job.result = result
            job.status = JobStatus.CANCELLED if job.cancelled else JobStatus.DONE
        except Exception as e:
            # P15: a background worker that dies without writing a terminal
            # status leaves the caller polling "running" forever.
            job.status = JobStatus.ERROR
            job.error = f"{type(e).__name__}: {e}"
            logger.error("Job %s (%s) failed: %s\n%s", job.id, job.kind, e,
                         traceback.format_exc())
        finally:
            # A BaseException (KeyboardInterrupt, SystemExit) skips the except
            # clause above and would otherwise leave the job on "running"
            # forever — the hung spinner P15 is about. Settle it here, then let
            # the exception continue to propagate.
            if job.status not in TERMINAL:
                job.status = JobStatus.ERROR
                job.error = job.error or "worker exited without completing"
                logger.error("Job %s (%s) exited without a terminal status",
                             job.id, job.kind)
            job.finished_at = time.time()
            if on_finish is not None:
                try:
                    on_finish(job)
                except Exception:
                    # A cleanup failure must not overwrite the job's verdict.
                    logger.exception("Job %s finish hook failed", job.id)

    # ── Polling ───────────────────────────────────────────────────────────────

    def get(self, job_id: str, owner: Optional[str] = None) -> Optional[Job]:
        """Return a job, or None if it is unknown, expired, or someone else's.

        Ownership is checked here rather than at each call site so a job id
        guessed or copied between users does not leak another user's results.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if owner is not None and job.owner != owner:
            return None
        return job

    def cancel(self, job_id: str, owner: Optional[str] = None) -> bool:
        job = self.get(job_id, owner)
        if job is None or job.status in TERMINAL:
            return False
        job.request_cancel()
        if job.status == JobStatus.QUEUED:
            # Never started; settle it now rather than waiting for a worker.
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
        return True

    def jobs_for(self, owner: str) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.owner == owner]

    # ── Expiry ────────────────────────────────────────────────────────────────

    def _expire_old(self) -> int:
        """Drop finished jobs older than the TTL. Returns how many."""
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            stale = [
                jid for jid, j in self._jobs.items()
                if j.finished_at is not None and j.finished_at < cutoff
            ]
            for jid in stale:
                del self._jobs[jid]
        if stale:
            logger.debug("Expired %d finished job(s)", len(stale))
        return len(stale)

    def shutdown(self, wait: bool = False) -> None:
        for job in list(self._jobs.values()):
            if job.status not in TERMINAL:
                job.request_cancel()
        self._pool.shutdown(wait=wait)
