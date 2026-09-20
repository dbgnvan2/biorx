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
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .tokens import UNCOUNTED, TokenUsage

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


class JobLookup:
    """Why a job could not be returned.

    Three different answers that must not collapse into one: a user whose job
    aged out should be told to run it again, not told it never existed, and a
    job belonging to someone else must be indistinguishable from an unknown id
    so a guessed id cannot confirm another user's activity.
    """
    FOUND = "found"
    UNKNOWN = "unknown"
    EXPIRED = "expired"


# How many expired job ids to remember, so an expired job can be reported as
# expired rather than unknown. Bounded: this is a courtesy, not a record.
EXPIRED_MEMORY = 512


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
    # Enrichment progress, kept apart from fetched/total so the fetched count
    # survives the enrichment phase (plan 2026-09-18 FR3).
    enriched: int = 0
    enrich_total: int = 0
    # Enrichment services whose lookups failed: label -> [failed, attempted].
    # Results are complete, but PDF links / filled-in metadata may be missing.
    enrich_problems: Dict[str, List[int]] = field(default_factory=dict)
    error: str = ""
    result: Any = None
    # What the model call this job made cost (M1.A.1). UNCOUNTED until a
    # provider reports something — uncounted means unknown, never free.
    token_usage: TokenUsage = field(default_factory=lambda: UNCOUNTED)
    # Sources that failed mid-run. The orchestrator swallows these per source;
    # surfacing them is what separates "no new papers" from "arXiv was down".
    sources_failed: List[str] = field(default_factory=list)
    # Why each failed source failed, in plain language, keyed by display label
    # (D2). "Could not reach X" alone read the same for an outage and a limit.
    source_problems: Dict[str, str] = field(default_factory=dict)
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
            "enriched": self.enriched,
            "enrich_total": self.enrich_total,
            "enrich_problems": {k: list(v) for k, v in self.enrich_problems.items()},
            "error": self.error,
            "sources_failed": list(self.sources_failed),
            "source_problems": dict(self.source_problems),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "tokens": {
                "prompt": self.token_usage.prompt,
                "completion": self.token_usage.completion,
                "total": self.token_usage.total,
                "counted": self.token_usage.counted,
            },
        }


class JobRegistry:
    """Submit, poll, cancel and expire background jobs."""

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._jobs: Dict[str, Job] = {}
        self._expired: "OrderedDict[str, str]" = OrderedDict()   # job id -> owner
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

    def lookup(self, job_id: str, owner: str) -> tuple:
        """Return (job_or_None, JobLookup reason).

        `owner` is required — not defaulted — so a caller cannot accidentally
        get unrestricted access by omitting it. Another user's job is reported
        as UNKNOWN, not as a permission error, so a guessed id cannot confirm
        that someone else's job exists.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            expired_owner = self._expired.get(job_id)
        if job is not None and job.owner == owner:
            return job, JobLookup.FOUND
        if job is None and expired_owner == owner:
            return None, JobLookup.EXPIRED
        return None, JobLookup.UNKNOWN

    def get(self, job_id: str, owner: str) -> Optional[Job]:
        """This user's job, or None. `owner` is required (see lookup())."""
        job, _ = self.lookup(job_id, owner)
        return job

    def cancel(self, job_id: str, owner: str) -> bool:
        job = self.get(job_id, owner)
        if job is None or job.status in TERMINAL:
            return False
        job.request_cancel()
        if job.status == JobStatus.QUEUED:
            # Never started; settle it now rather than waiting for a worker.
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
        return True

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
                self._expired[jid] = self._jobs[jid].owner
                del self._jobs[jid]
            while len(self._expired) > EXPIRED_MEMORY:
                self._expired.popitem(last=False)
        if stale:
            logger.debug("Expired %d finished job(s)", len(stale))
        return len(stale)

    def shutdown(self, wait: bool = True, timeout: float = 10.0) -> bool:
        """Cancel everything and wait for the workers to stop. Returns whether
        they all stopped.

        The caller must not tear down shared resources until this returns True.
        Closing the database while a worker still holds a connection to it is
        not an exception — it is a segmentation fault, and it was reproduced
        here once in five runs before the wait existed.
        """
        for job in list(self._jobs.values()):
            if job.status not in TERMINAL:
                job.request_cancel()
        self._pool.shutdown(wait=False)
        if not wait:
            return False

        deadline = time.time() + timeout
        while time.time() < deadline:
            running = [j for j in list(self._jobs.values()) if j.status not in TERMINAL]
            if not running:
                return True
            time.sleep(0.01)

        still = [j.id for j in list(self._jobs.values()) if j.status not in TERMINAL]
        logger.error(
            "Job registry did not drain within %.1fs; %d job(s) still running "
            "(%s). Shared resources they hold must NOT be closed.",
            timeout, len(still), ", ".join(still[:5]),
        )
        return False
