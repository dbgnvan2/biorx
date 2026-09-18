"""
Tests for the async search routes.

Spec: docs/implementation_plan_2026-09-15.md#2.2, #2.3
The orchestrator is replaced with a double: these test the route and job
plumbing, not the retrieval pipeline, which has its own suite.
"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src.sources.schema import AuthorRecord, CanonicalRecord, RecordFlags, SourceHit
from tests.web.conftest import ACCESS_CODE

FILTER = {
    "days_back": 14,
    "text_groups": [{"title": "", "abstract": "", "both": "generative agents"}],
    "authors": [],
}


def _record(title, abstract="generative agents in simulation"):
    return CanonicalRecord(
        canonical_id=f"arxiv:{abs(hash(title))}", title=title, abstract=abstract,
        authors=[AuthorRecord(display_name="Park J", sequence=1)],
        year=2026, published_date="2026-09-01", document_type="preprint",
        is_preprint=True, journal_or_server="arXiv", doi="", pmid="", pmcid="",
        source_url="https://arxiv.org/abs/x", best_oa_url="", pdf_url="",
        license="", oa_status="open", subjects=[], keywords=[],
        source_hits=[SourceHit(source="arxiv", source_record_id="x",
                               fetched_at="2026-09-01")],
        flags=RecordFlags(), source_trust_weight=0.75,
    )


def _fake_orchestrator(records=None, status_messages=(), block=None):
    """A stand-in that drives the real callback protocol."""
    records = records if records is not None else [_record("Generative Agents")]

    def search(filter_dict=None, source_selection=None, on_batch=None,
               on_progress=None, on_status=None, should_stop=None,
               max_results=200, **kwargs):
        for message in status_messages:
            if on_status:
                on_status(message)
        if block is not None:
            while not block.is_set():
                if should_stop and should_stop():
                    return []
                time.sleep(0.01)
        if on_progress:
            on_progress(len(records), len(records))
        if on_batch and records:
            on_batch(records)
        return records

    orch = MagicMock()
    orch.search.side_effect = search
    orch._search_adapters = {"arxiv": object(), "europepmc": object()}
    return orch


def _await_status(client, job_id, wanted=("done", "error", "cancelled"), timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/searches/{job_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(0.01)
    raise AssertionError(f"job never settled: {body}")


def test_a_search_runs_and_returns_matching_papers(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator()
    start = signed_in.post("/api/searches", json={"filter": FILTER})
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    _await_status(signed_in, job_id)
    results = signed_in.get(f"/api/searches/{job_id}/results").json()
    assert results["total"] == 1
    assert results["results"][0]["title"] == "Generative Agents"


def test_results_are_filtered_the_same_way_the_gui_filters_them(ctx, signed_in):
    """A record the source returned but the filter excludes must not be shown."""
    ctx.orchestrator = _fake_orchestrator(records=[
        _record("Generative Agents"),
        _record("Protein Folding", abstract="nothing to do with the filter"),
    ])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)
    results = signed_in.get(f"/api/searches/{job_id}/results").json()
    assert [r["title"] for r in results["results"]] == ["Generative Agents"]


def test_results_are_paginated(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator(
        records=[_record(f"Generative Agents {i}") for i in range(10)]
    )
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)

    page = signed_in.get(f"/api/searches/{job_id}/results?offset=0&limit=4").json()
    assert page["total"] == 10 and len(page["results"]) == 4
    rest = signed_in.get(f"/api/searches/{job_id}/results?offset=8&limit=4").json()
    assert len(rest["results"]) == 2


def test_a_search_can_run_from_a_saved_filter(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator()
    created = signed_in.post("/api/filters",
                             json={"name": "Saved", "filter": FILTER}).json()
    start = signed_in.post("/api/searches", json={"filter_id": created["id"]})
    assert start.status_code == 202
    _await_status(signed_in, start.json()["job_id"])


def test_a_search_needs_a_filter(signed_in):
    assert signed_in.post("/api/searches", json={}).status_code == 400


def test_an_unknown_saved_filter_is_404(signed_in):
    assert signed_in.post("/api/searches", json={"filter_id": 99999}).status_code == 404


# ── P2: a source that failed must not look like a quiet week ──────────────────

def test_a_failed_source_is_reported_not_silently_zero(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator(
        records=[], status_messages=["arXiv — skipped (unavailable)"]
    )
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert body["sources_failed"] == ["arxiv"]
    assert signed_in.get(f"/api/searches/{job_id}/results").json()["sources_failed"] \
        == ["arxiv"]


def test_a_normal_progress_message_is_not_read_as_a_failure(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator(
        status_messages=["arXiv: 40 fetched", "Searching Europe PMC…"]
    )
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert body["sources_failed"] == []


def test_the_failure_marker_matches_what_the_orchestrator_actually_emits():
    """
    Behavioral round-trip against the real producer (learnings P19): the orchestrator
    emits a human-readable status on source failure; source_from_failure_status parses
    it back to the internal source name. If someone changes the emission format without
    updating the parser, this fails at the on_status boundary, not in source text.
    """
    from src.sources.orchestrator import SourceOrchestrator, _SOURCE_LABELS
    from src.sources.errors import SourceUnavailableError
    from web.routes_searches import source_from_failure_status

    target_name = next(iter(_SOURCE_LABELS))
    target_label = _SOURCE_LABELS[target_name]

    class _AlwaysFails:
        source_name = target_name
        source_trust_weight = 1.0
        def search(self, *a, **kw): raise SourceUnavailableError("down for test")
        def normalize(self, raw): raise NotImplementedError

    orch = SourceOrchestrator({})
    orch._search_adapters = {target_name: _AlwaysFails()}

    msgs: list = []
    orch.search(
        filter_dict={"days_back": 1, "text_groups": [], "authors": []},
        source_selection={"all": False, "selected": [target_name]},
        on_status=msgs.append,
    )

    failure_msgs = [m for m in msgs if source_from_failure_status(m) != ""]
    assert failure_msgs, (
        f"Orchestrator emitted no parseable failure status for {target_name!r}: {msgs}"
    )
    assert source_from_failure_status(failure_msgs[0]) == target_name, (
        f"source_from_failure_status({failure_msgs[0]!r}) should be {target_name!r}"
    )
    # Progress-style messages must not look like failures.
    assert source_from_failure_status(f"{target_label}: 40 fetched") == ""


def test_the_failure_parser_handles_all_sources_and_qualifiers():
    """Unit test: source_from_failure_status covers all 8 _SOURCE_LABELS entries
    across all 4 qualifier variants (unavailable/error/rate-limited/partial).
    Progress messages must never parse as failures. Covers mutation gap where
    only next(iter()) was tested in the behavioral round-trip above."""
    from src.sources.orchestrator import FAILURE_STATUS_MARKER, _SOURCE_LABELS
    from web.routes_searches import source_from_failure_status

    qualifiers = ("unavailable", "error", "rate-limited", "partial")
    for name, label in _SOURCE_LABELS.items():
        for q in qualifiers:
            msg = f"{label} {FAILURE_STATUS_MARKER} ({q})"
            assert source_from_failure_status(msg) == name, (
                f"Parser failed for source={name!r} qualifier={q!r}: {msg!r}"
            )
        assert source_from_failure_status(f"{label}: 40 fetched") == "", (
            f"Progress message misidentified as failure for source={name!r}"
        )


# ── Cancellation, expiry and ownership ────────────────────────────────────────

def test_a_search_can_be_cancelled(ctx, signed_in):
    block = threading.Event()
    ctx.orchestrator = _fake_orchestrator(block=block)
    try:
        job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
        time.sleep(0.05)
        assert signed_in.delete(f"/api/searches/{job_id}").status_code == 200
        body = _await_status(signed_in, job_id)
        assert body["status"] == "cancelled"
    finally:
        block.set()


def test_an_unknown_job_is_404(signed_in):
    assert signed_in.get("/api/searches/nope").status_code == 404


def test_an_expired_job_is_410_not_404(ctx, signed_in):
    """A user whose search aged out is told to run it again, not that it never
    existed (chunk-3 gate finding 1)."""
    ctx.orchestrator = _fake_orchestrator()
    ctx.jobs.ttl_seconds = 0
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)
    signed_in.post("/api/searches", json={"filter": FILTER})     # triggers the sweep
    r = signed_in.get(f"/api/searches/{job_id}")
    assert r.status_code == 410
    assert "run it again" in r.json()["detail"]


def test_one_user_cannot_read_or_cancel_anothers_search(ctx, app):
    ctx.orchestrator = _fake_orchestrator()
    alice = TestClient(app)
    if True:
        alice.post("/api/session", json={"access_code": ACCESS_CODE})
        job_id = alice.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
        _await_status(alice, job_id)

    bob = TestClient(app)
    if True:
        bob.post("/api/session", json={"access_code": ACCESS_CODE})
        assert bob.get(f"/api/searches/{job_id}").status_code == 404
        assert bob.get(f"/api/searches/{job_id}/results").status_code == 404
        assert bob.delete(f"/api/searches/{job_id}").status_code == 404


# ── D2: a failed source carries a plain-language reason (2026-09-18) ─────────

@pytest.mark.parametrize("kind,expect", [
    ("unavailable", "did not respond properly"),
    ("rate-limited", "limiting how fast"),
    ("error", "unexpected error"),
    ("partial", "part-way"),
    ("something-new", "could not be reached"),     # unknown kind: honest fallback
])
def test_d2_failure_reason_from_config(kind, expect):
    from src.sources.config import load_sources_config
    from src.sources.orchestrator import FAILURE_STATUS_MARKER, _SOURCE_LABELS
    from web.routes_searches import failure_reason
    msg = f"{_SOURCE_LABELS['biorxiv_medrxiv']} {FAILURE_STATUS_MARKER} ({kind})"
    assert expect in failure_reason(msg, load_sources_config())


def test_d2_job_records_label_and_reason_once():
    from src.jobs import Job
    from src.sources.config import load_sources_config
    from src.sources.orchestrator import FAILURE_STATUS_MARKER, _SOURCE_LABELS
    from web.routes_searches import record_failure
    job = Job(id="j", kind="search", owner="u")
    label = _SOURCE_LABELS["biorxiv_medrxiv"]
    for _ in range(3):
        record_failure(job, f"{label} {FAILURE_STATUS_MARKER} (unavailable)", load_sources_config())
    record_failure(job, "Searching Europe PMC… page 2", load_sources_config())
    assert job.sources_failed == ["biorxiv_medrxiv"]
    assert list(job.source_problems) == [label]
    assert "did not respond properly" in job.source_problems[label]
    assert "source_problems" in job.to_dict()
