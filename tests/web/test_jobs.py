"""
Tests for src/jobs.py — the background job registry.

Spec: docs/implementation_plan_2026-09-15.md#2.3
The rules that matter here are P15 (a worker must never exit leaving "running")
and P2 (a handled failure that lost data must still be visible).
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src.jobs import Job, JobRegistry, JobStatus, TERMINAL


@pytest.fixture
def registry():
    r = JobRegistry(max_workers=2)
    yield r
    r.shutdown()


def _settled(job, timeout=5.0):
    """Wait for a job to reach a terminal status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in TERMINAL:
            return job.status
        time.sleep(0.005)
    raise AssertionError(f"job never settled; stuck at {job.status!r}")


# ── Happy path ────────────────────────────────────────────────────────────────

def test_a_job_runs_and_carries_its_result(registry):
    job = registry.submit("search", "u1", lambda j: {"papers": 3})
    assert _settled(job) == JobStatus.DONE
    assert job.result == {"papers": 3}
    assert job.finished_at is not None


def test_a_job_can_report_progress(registry):
    def work(j):
        j.phase = "Europe PMC"
        j.fetched, j.total = 40, 120
        return None

    job = registry.submit("search", "u1", work)
    _settled(job)
    assert job.to_dict()["phase"] == "Europe PMC"
    assert job.to_dict()["fetched"] == 40


# ── P15: the worker guard ─────────────────────────────────────────────────────

def test_worker_exception_sets_error_status_not_running(registry):
    def boom(j):
        raise ValueError("source exploded")

    job = registry.submit("search", "u1", boom)
    assert _settled(job) == JobStatus.ERROR
    assert "ValueError" in job.error and "source exploded" in job.error
    assert job.status != JobStatus.RUNNING


def test_setup_failure_before_running_is_also_guarded(registry):
    """
    An exception raised on the worker's very first line — before any status
    update of its own — must still land on "error", not leave "queued".
    """
    def fail_immediately(j):
        raise RuntimeError("config read failed")

    job = registry.submit("summary", "u1", fail_immediately)
    assert _settled(job) == JobStatus.ERROR
    assert "config read failed" in job.error


def test_a_baseexception_does_not_leave_the_job_running(registry):
    def bad(j):
        raise KeyboardInterrupt()

    job = registry.submit("search", "u1", bad)
    deadline = time.time() + 2
    while time.time() < deadline and job.status == JobStatus.RUNNING:
        time.sleep(0.01)
    assert job.status != JobStatus.RUNNING, "job stuck on running after BaseException"


def test_a_failing_finish_hook_does_not_overwrite_the_verdict(registry):
    def hook(j):
        raise RuntimeError("cleanup failed")

    job = registry.submit("search", "u1", lambda j: "value", on_finish=hook)
    assert _settled(job) == JobStatus.DONE
    assert job.result == "value"


def test_the_finish_hook_runs_on_success_and_on_failure(registry):
    seen = []
    registry.submit("a", "u1", lambda j: None, on_finish=lambda j: seen.append(j.status))
    registry.submit("b", "u1", lambda j: 1 / 0, on_finish=lambda j: seen.append(j.status))
    deadline = time.time() + 5
    while time.time() < deadline and len(seen) < 2:
        time.sleep(0.01)
    assert sorted(seen) == [JobStatus.DONE, JobStatus.ERROR]


# ── P2: a handled failure that lost data stays visible ────────────────────────

def test_failed_source_is_reported_on_the_job_not_silently_zero(registry):
    def work(j):
        j.sources_failed.append("arxiv")
        return []

    job = registry.submit("search", "u1", work)
    _settled(job)
    assert job.to_dict()["sources_failed"] == ["arxiv"]
    assert job.status == JobStatus.DONE      # the run completed; data was lost


# ── Cancellation ──────────────────────────────────────────────────────────────

def test_cancel_stops_the_worker_through_should_stop(registry):
    started = threading.Event()

    def work(j):
        started.set()
        for _ in range(500):
            if j.should_stop():
                return "stopped early"
            time.sleep(0.005)
        return "ran to completion"

    job = registry.submit("search", "u1", work)
    started.wait(timeout=5)
    assert registry.cancel(job.id) is True
    assert _settled(job) == JobStatus.CANCELLED
    assert job.result == "stopped early"


def test_cancelling_a_queued_job_settles_it_immediately():
    r = JobRegistry(max_workers=1)
    try:
        block = threading.Event()
        r.submit("search", "u1", lambda j: block.wait(timeout=5))
        queued = r.submit("search", "u1", lambda j: "never")
        assert r.cancel(queued.id) is True
        assert queued.status == JobStatus.CANCELLED
        block.set()
    finally:
        r.shutdown()


def test_cancelling_a_finished_job_reports_false(registry):
    job = registry.submit("search", "u1", lambda j: None)
    _settled(job)
    assert registry.cancel(job.id) is False


# ── Ownership and expiry ──────────────────────────────────────────────────────

def test_another_user_cannot_read_or_cancel_a_job(registry):
    job = registry.submit("search", "owner-user", lambda j: "secret results")
    _settled(job)
    assert registry.get(job.id, owner="someone-else") is None
    assert registry.cancel(job.id, owner="someone-else") is False
    assert registry.get(job.id, owner="owner-user") is job


def test_an_unknown_job_id_is_reported_as_missing(registry):
    assert registry.get("does-not-exist", owner="u1") is None


def test_finished_jobs_expire_after_their_ttl():
    r = JobRegistry(max_workers=1, ttl_seconds=0)
    try:
        job = r.submit("search", "u1", lambda j: None)
        _settled(job)
        r.submit("search", "u1", lambda j: None)   # submitting sweeps
        assert r.get(job.id) is None
    finally:
        r.shutdown()


def test_a_running_job_is_never_expired():
    r = JobRegistry(max_workers=2, ttl_seconds=0)
    try:
        block = threading.Event()
        long_job = r.submit("search", "u1", lambda j: block.wait(timeout=5))
        time.sleep(0.05)
        r.submit("search", "u1", lambda j: None)   # triggers the sweep
        assert r.get(long_job.id) is long_job
        block.set()
    finally:
        r.shutdown()


def test_jobs_for_lists_only_that_users_jobs(registry):
    a = registry.submit("search", "u1", lambda j: None)
    b = registry.submit("search", "u2", lambda j: None)
    _settled(a); _settled(b)
    assert [j.id for j in registry.jobs_for("u1")] == [a.id]


def test_shutdown_cancels_unfinished_jobs():
    r = JobRegistry(max_workers=1)
    block = threading.Event()
    job = r.submit("search", "u1", lambda j: block.wait(timeout=5))
    time.sleep(0.05)
    r.shutdown(wait=False)
    assert job.cancelled
    block.set()
