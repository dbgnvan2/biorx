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
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src.sources.schema import AuthorRecord, CanonicalRecord, RecordFlags, SourceHit
from tests.web.conftest import account_body

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


def _fake_orchestrator(records=None, status_messages=(), block=None, failures=()):
    """A stand-in that drives the real callback protocol. `failures` are
    (source name, kind) pairs reported through on_source_failure (review S2)."""
    records = records if records is not None else [_record("Generative Agents")]

    def search(filter_dict=None, source_selection=None, on_batch=None,
               on_progress=None, on_status=None, should_stop=None,
               max_results=200, on_source_failure=None, **kwargs):
        for message in status_messages:
            if on_status:
                on_status(message)
        for name, kind in failures:
            on_source_failure(name, kind)
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


EMPTY_FILTER = {"days_back": 14, "category": "neuroscience",
                "text_groups": [{"title": "", "abstract": " ", "both": ""}],
                "authors": [], "institution": ""}


def test_fr1_1_empty_saved_filter_is_refused(ctx, signed_in):
    """FR1.1: an empty saved filter is refused before any job is queued."""
    ctx.orchestrator = _fake_orchestrator()
    created = signed_in.post("/api/filters",
                             json={"name": "Empty", "filter": EMPTY_FILTER}).json()
    start = signed_in.post("/api/searches", json={"filter_id": created["id"]})
    assert start.status_code == 400
    assert "no search terms" in start.json()["detail"]
    assert "job_id" not in start.json()
    ctx.orchestrator.search.assert_not_called()


def test_fr1_2_empty_inline_filter_is_refused(ctx, signed_in):
    """FR1.2: the manual search form cannot start an empty search either."""
    ctx.orchestrator = _fake_orchestrator()
    start = signed_in.post("/api/searches", json={"filter": EMPTY_FILTER})
    assert start.status_code == 400
    ctx.orchestrator.search.assert_not_called()


# ── FR2 / FR3: enrichment through the real orchestrator, network faked ────────

def _real_orchestrator(records):
    """The real SourceOrchestrator (dedup, page loop, enrichment order) with a
    fake adapter and fake Crossref/Unpaywall. A fake orchestrator would skip the
    very stage these tests are about (learnings P36 corollary)."""
    from src.sources.orchestrator import SourceOrchestrator
    orch = SourceOrchestrator.__new__(SourceOrchestrator)
    orch.config = {}
    orch.warnings = []
    adapter = MagicMock()
    adapter.search.return_value = [{} for _ in records]
    adapter.normalize.side_effect = list(records)
    adapter.last_page_size = len(records)
    adapter.last_total = len(records)
    orch._search_adapters = {"europepmc": adapter}
    orch._crossref = MagicMock()

    def unpaywall_enrich(record):
        record.pdf_url = f"https://oa.example/{record.doi}.pdf"
        record.best_oa_url = record.pdf_url
    orch._unpaywall = MagicMock()
    orch._unpaywall.enrich.side_effect = unpaywall_enrich
    return orch


def _doi_record(title, doi, abstract="generative agents in simulation"):
    r = _record(title, abstract=abstract)
    r.doi = doi
    r.canonical_id = f"doi:{doi}"
    return r


def test_fr2_3_results_include_enriched_fields(ctx, signed_in):
    """FR2.3: what enrichment finds reaches the results the user gets."""
    ctx.orchestrator = _real_orchestrator([_doi_record("Generative Agents", "10.1/a")])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)
    results = signed_in.get(f"/api/searches/{job_id}/results").json()["results"]
    assert [r["pdf_url"] for r in results] == ["https://oa.example/10.1/a.pdf"]


def test_fr2_4_no_match_means_no_enrichment(ctx, signed_in):
    """FR2.4: papers the filter drops are never enriched; a run that matches
    one of three makes calls for that one only, and one that matches none
    makes none."""
    orch = _real_orchestrator([
        _doi_record("Generative Agents", "10.1/a"),
        _doi_record("Protein Folding", "10.1/b", abstract="kinetics"),
        _doi_record("Soil Carbon", "10.1/c", abstract="farming"),
    ])
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)
    assert [c.args[0].doi for c in orch._crossref.enrich.call_args_list] == ["10.1/a"]
    assert [c.args[0].doi for c in orch._unpaywall.enrich.call_args_list] == ["10.1/a"]

    orch = _real_orchestrator([_doi_record("Protein Folding", "10.1/b", abstract="kinetics")])
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert body["matched"] == 0
    orch._crossref.enrich.assert_not_called()
    orch._unpaywall.enrich.assert_not_called()


def test_fr3_1_fetched_survives_enrichment(ctx, signed_in):
    """FR3.1: three fetched, one matched and enriched. The poll must still say
    three fetched — enrichment used to overwrite it with its own count."""
    ctx.orchestrator = _real_orchestrator([
        _doi_record("Generative Agents", "10.1/a"),
        _doi_record("Protein Folding", "10.1/b", abstract="kinetics"),
        _doi_record("Soil Carbon", "10.1/c", abstract="farming"),
    ])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert (body["fetched"], body["matched"], body["enriched"], body["enrich_total"]) == (3, 1, 1, 1)


# ── P2: a source that failed must not look like a quiet week ──────────────────

def test_a_failed_source_is_reported_not_silently_zero(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator(
        records=[], status_messages=["arXiv — skipped (unavailable)"],
        failures=[("arxiv", "unavailable")],
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


def test_s2_web_records_failures_from_the_real_orchestrator(ctx, signed_in):
    """Round trip through the real orchestrator (learnings P19): a failing
    source reaches the job as its internal name and a plain-language reason,
    with no parsing of status text (review S2 replaced the string parser)."""
    from src.sources.orchestrator import SourceOrchestrator
    from src.sources.errors import SourceUnavailableError
    from src.sources.config import SOURCE_LABELS

    class _AlwaysFails:
        def search(self, *a, **kw): raise SourceUnavailableError("down for test")
        def normalize(self, raw): raise NotImplementedError

    orch = SourceOrchestrator({})
    orch._search_adapters = {name: _AlwaysFails() for name in ("arxiv", "biorxiv_medrxiv")}
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert sorted(body["sources_failed"]) == ["arxiv", "biorxiv_medrxiv"]
    assert set(body["source_problems"]) == {SOURCE_LABELS["arxiv"], SOURCE_LABELS["biorxiv_medrxiv"]}


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
    # With a zero TTL the job expires as soon as it finishes; since review A14
    # a poll sweeps too, so no second submit is needed to see the 410.
    deadline = time.time() + 5
    r = signed_in.get(f"/api/searches/{job_id}")
    while r.status_code == 200 and time.time() < deadline:
        time.sleep(0.01)
        r = signed_in.get(f"/api/searches/{job_id}")
    assert r.status_code == 410
    assert "run it again" in r.json()["detail"]


def test_one_user_cannot_read_or_cancel_anothers_search(ctx, app):
    ctx.orchestrator = _fake_orchestrator()
    alice = TestClient(app)
    if True:
        alice.post("/api/session", json=account_body())
        job_id = alice.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
        _await_status(alice, job_id)

    bob = TestClient(app)
    if True:
        bob.post("/api/session", json=account_body())
        assert bob.get(f"/api/searches/{job_id}").status_code == 404
        assert bob.get(f"/api/searches/{job_id}/results").status_code == 404
        assert bob.delete(f"/api/searches/{job_id}").status_code == 404


# ── D2: a failed source carries a plain-language reason (2026-09-18) ─────────

@pytest.mark.parametrize("kind,expect", [
    ("unavailable", "did not respond properly"),
    ("rate-limited", "limiting how fast"),
    ("error", "unexpected error"),
    ("partial", "part-way"),
    ("truncated", "raise the limit"),       # B1 (renamed from "Max results", FL)
    ("something-new", "could not be reached"),     # unknown kind: honest fallback
])
def test_d2_failure_reason_from_config(kind, expect):
    from src.sources.config import load_sources_config
    from web.routes_searches import failure_reason
    assert expect in failure_reason(kind, load_sources_config())


def test_d2_job_records_label_and_reason_once():
    from src.jobs import Job
    from src.sources.config import load_sources_config, source_label
    from web.routes_searches import record_failure
    job = Job(id="j", kind="search", owner="u")
    for _ in range(3):
        record_failure(job, "biorxiv_medrxiv", "unavailable", load_sources_config())
    label = source_label("biorxiv_medrxiv")
    assert job.sources_failed == ["biorxiv_medrxiv"]
    assert list(job.source_problems) == [label]
    assert "did not respond properly" in job.source_problems[label]
    assert "source_problems" in job.to_dict()


def test_i1_saved_filter_sources_reach_the_orchestrator(ctx, signed_in):
    """Issue 1 (server half): with no source_selection in the request, a saved
    filter searches the sources saved with it."""
    ctx.orchestrator = _fake_orchestrator()
    own = {"all": False, "selected": ["arxiv"]}
    created = signed_in.post("/api/filters", json={
        "name": "Own sources", "filter": {**FILTER, "source_selection": own}}).json()
    job_id = signed_in.post("/api/searches", json={"filter_id": created["id"]}).json()["job_id"]
    _await_status(signed_in, job_id)
    assert ctx.orchestrator.search.call_args.kwargs["source_selection"] == own


def test_i3_enrichment_outage_reaches_the_poll(ctx, signed_in):
    """Issue 3: a Crossref outage during a search is in the poll payload, so
    the page can say why PDF links or details may be missing."""
    orch = _real_orchestrator([_doi_record("Generative Agents", "10.1/a")])
    orch._crossref.enrich.return_value = False
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert body["enrich_problems"] == {"Crossref": [1, 1]}


# ── B5: the licence filter sees the licence enrichment found ─────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#B5

def test_b5_license_filter_sees_enriched_license(ctx, signed_in):
    """Europe PMC often reports no licence; Unpaywall supplies one. Filtering
    before enrichment dropped such papers, and never enriched them either."""
    orch = _real_orchestrator([
        _doi_record("Generative Agents A", "10.1/a"),     # Unpaywall says cc-by
        _doi_record("Generative Agents B", "10.1/b"),     # Unpaywall says cc-by-nc
    ])
    licences = {"10.1/a": "cc-by", "10.1/b": "cc-by-nc"}

    def unpaywall_enrich(record):
        record.license = licences[record.doi]
    orch._unpaywall.enrich.side_effect = unpaywall_enrich
    ctx.orchestrator = orch

    job_id = signed_in.post("/api/searches",
                            json={"filter": dict(FILTER, license="cc-by")}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    results = signed_in.get(f"/api/searches/{job_id}/results").json()["results"]
    assert [r["doi"] for r in results] == ["10.1/a"]
    assert body["matched"] == 1
    assert sorted(c.args[0].doi for c in orch._unpaywall.enrich.call_args_list) == ["10.1/a", "10.1/b"]


# ── M11: summaries for many papers in a few queries, one card shape ──────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M11

def test_m11_summaries_bulk_query_count(ctx):
    from src.jobs import Job
    from web.routes_searches import _job_summaries
    papers = [{"doi": f"10.9/{i}", "canonical_id": f"doi:10.9/{i}", "title": f"P{i}"}
              for i in range(2000)]
    for p in papers[:300]:
        pid = ctx.db.insert_paper(p)
        ctx.db.insert_summary(pid, summary_text="", key_findings=["f"], source_text="full_text")
    job = Job(id="j", kind="search", owner="u")
    job.result = papers
    statements = []
    ctx.db.conn.set_trace_callback(statements.append)
    try:
        items, summaries = _job_summaries(ctx, job)
    finally:
        ctx.db.conn.set_trace_callback(None)
    assert len(items) == 2000 and len(summaries) == 300
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    # Chunks of 500 (SQLite's bound-parameter limit): 4 by DOI + 4 by
    # canonical_id + 1 for the summaries. It was ~3 queries per result (~6000).
    import math
    assert len(selects) <= 2 * math.ceil(2000 / 500) + math.ceil(300 / 500), len(selects)


def test_m11_card_identical_across_endpoints(ctx, signed_in):
    from src.jobs import JobStatus
    me = signed_in.get("/api/me").json()["user_id"]
    paper = {"doi": "10.9/card", "canonical_id": "doi:10.9/card", "title": "Card"}
    pid = ctx.db.insert_paper(paper)
    ctx.db.insert_summary(pid, summary_text="", key_findings=["f"], methodology="m",
                          conclusions="c", model_version="mv", source_text="full_text")
    job = ctx.jobs.submit("search", me, lambda j: [dict(paper)])
    for _ in range(200):
        if job.status == JobStatus.DONE:
            break
        time.sleep(0.01)
    from_search = signed_in.get(f"/api/searches/{job.id}/summaries").json()["summaries"]
    lst = signed_in.post(f"/api/searches/{job.id}/save-as-list", json={"name": "Cards"}).json()
    from_list = signed_in.get(f"/api/references/{lst['id']}/summaries").json()["summaries"]
    assert len(from_search) == len(from_list) == 1
    list_card = dict(from_list[0])
    list_card.pop("item_id")
    assert list_card == from_search[0]


# ── SW2–SW6: search within results ────────────────────────────────────────────
# docs/implementation_plan_2026-10-07_search_within.md

SW_PAPERS = [
    {"canonical_id": "doi:10.9/a", "doi": "10.9/a", "title": "Infant cortisol and sleep",
     "abstract": "Maternal stress."},
    {"canonical_id": "doi:10.9/b", "doi": "10.9/b", "title": "Cortisol in adolescents",
     "abstract": "Sleep loss."},
    {"canonical_id": "doi:10.9/c", "doi": "10.9/c", "title": "Infant feeding",
     "abstract": "Breast milk.", "journal": "Cortisol Letters"},
    {"canonical_id": "doi:10.9/d", "doi": "10.9/d", "title": "Melatonin and infants",
     "abstract": "Sleep onset."},
]


def _finished_job(ctx, client, papers=SW_PAPERS):
    from src.jobs import JobStatus
    me = client.get("/api/me").json()["user_id"]
    job = ctx.jobs.submit("search", me, lambda j: [dict(p) for p in papers])
    for _ in range(300):
        if job.status == JobStatus.DONE:
            return job
        time.sleep(0.01)
    raise AssertionError("job never finished")


def _ids(body):
    return [r["canonical_id"] for r in body["results"]]


def test_sw2_within_narrows_and_pages(ctx, signed_in):
    job = _finished_job(ctx, signed_in)
    url = f"/api/searches/{job.id}/results"
    one = signed_in.get(url, params={"within": ["cortisol"]}).json()
    assert _ids(one) == ["doi:10.9/a", "doi:10.9/b"]           # not c: journal only
    assert one["total"] == 2 and one["total_unrefined"] == 4
    assert one["within"] == ["cortisol"]
    two = signed_in.get(url, params={"within": ["cortisol", "infant*"]}).json()
    assert _ids(two) == ["doi:10.9/a"]                        # AND across terms
    alt = signed_in.get(url, params={"within": ["cortisol, melatonin", "sleep"]}).json()
    assert _ids(alt) == ["doi:10.9/a", "doi:10.9/b", "doi:10.9/d"]
    paged = signed_in.get(url, params={"within": ["sleep"], "offset": 1, "limit": 1}).json()
    assert paged["total"] == 3 and _ids(paged) == ["doi:10.9/b"]


def test_sw2_no_within_is_unchanged(ctx, signed_in):
    job = _finished_job(ctx, signed_in)
    body = signed_in.get(f"/api/searches/{job.id}/results").json()
    assert body["total"] == body["total_unrefined"] == 4 and body["within"] == []
    assert _ids(body) == [p["canonical_id"] for p in SW_PAPERS]


def test_sw3_within_makes_no_source_calls(ctx, signed_in):
    ctx.orchestrator = _fake_orchestrator(records=[_record("Generative Agents")])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    _await_status(signed_in, job_id)
    calls = ctx.orchestrator.search.call_count
    for terms in (["generative"], ["agents", "simulation"], ["nothing"]):
        signed_in.get(f"/api/searches/{job_id}/results", params={"within": terms})
    assert ctx.orchestrator.search.call_count == calls == 1


def test_sw4_save_all_with_within_saves_the_refined_set(ctx, signed_in):
    job = _finished_job(ctx, signed_in)
    r = signed_in.post(f"/api/searches/{job.id}/save-as-list",
                       json={"name": "Refined", "within": ["cortisol", "infant*"]})
    assert r.status_code == 201, r.text
    assert r.json()["saved"] == 1
    items = signed_in.get(f"/api/references/{r.json()['id']}/items").json()["items"]
    assert [i["paper"]["title"] for i in items] == ["Infant cortisol and sleep"]


def test_sw4_ticked_papers_win_over_within(ctx, signed_in):
    job = _finished_job(ctx, signed_in)
    r = signed_in.post(f"/api/searches/{job.id}/save-as-list",
                       json={"name": "Ticked", "paper_ids": ["doi:10.9/d"],
                             "within": ["cortisol"]})
    assert r.json()["saved"] == 1
    items = signed_in.get(f"/api/references/{r.json()['id']}/items").json()["items"]
    assert [i["paper"]["title"] for i in items] == ["Melatonin and infants"]


def test_sw5_summaries_and_pdf_follow_within(ctx, signed_in):
    for p in SW_PAPERS:
        pid = ctx.db.insert_paper(p)
        ctx.db.insert_summary(pid, summary_text="", key_findings=[p["title"]],
                              source_text="full_text")
    job = _finished_job(ctx, signed_in)
    body = signed_in.get(f"/api/searches/{job.id}/summaries",
                         params={"within": ["cortisol"]}).json()
    assert body["total_results"] == 2 and len(body["summaries"]) == 2
    from web import routes_searches
    seen = {}
    real = routes_searches._job_summaries

    def spy(ctx_, job_, only_ids=None, within=None):
        items, summaries = real(ctx_, job_, only_ids, within)
        seen["titles"] = [i["paper"]["title"] for i in items]
        return items, summaries
    with patch.object(routes_searches, "_job_summaries", spy):
        r = signed_in.post(f"/api/searches/{job.id}/summaries.pdf",
                           json={"title": "x", "within": ["melatonin"]})
    assert r.status_code == 200
    assert seen["titles"] == ["Melatonin and infants"]


def test_sw6_limits_are_enforced(ctx, signed_in):
    job = _finished_job(ctx, signed_in)
    url = f"/api/searches/{job.id}/results"
    assert signed_in.get(url, params={"within": [f"t{i}" for i in range(11)]}).status_code == 422
    assert signed_in.get(url, params={"within": ["x" * 201]}).status_code == 422
    r = signed_in.post(f"/api/searches/{job.id}/save-as-list",
                       json={"name": "Too many", "within": [f"t{i}" for i in range(11)]})
    assert r.status_code == 422
    ok = signed_in.get(url, params={"within": ["", "  ", "AND"]}).json()
    assert ok["total"] == 4                                   # ignored terms


def test_sw6_real_scale_is_fast(ctx, signed_in):
    """P9: the cap's worth of papers (2,000), three terms."""
    papers = [{"canonical_id": f"doi:10.9/{i}", "title": f"Paper {i} on sleep",
               "abstract": ("cortisol " if i % 3 == 0 else "") + "infant study " * 40}
              for i in range(2000)]
    job = _finished_job(ctx, signed_in, papers)
    start = time.perf_counter()
    body = signed_in.get(f"/api/searches/{job.id}/results",
                         params={"within": ["sleep", "cortisol", "infant* AND study"]}).json()
    elapsed = time.perf_counter() - start
    assert body["total"] == 667 and body["total_unrefined"] == 2000
    assert elapsed < 0.5, elapsed


# ── BW5: the job says how bioRxiv/medRxiv is read for the range ──────────────
# docs/implementation_plan_2026-10-07_biorxiv_window.md

def _bw_search(ctx, client, start, end, active):
    from datetime import date
    ctx.orchestrator = _fake_orchestrator(records=[])
    ctx.orchestrator.resolve_active_sources = lambda selection: list(active)
    fd = {**FILTER, "days_back": 0, "start_date": start, "end_date": end}
    job_id = client.post("/api/searches", json={"filter": fd}).json()["job_id"]
    return _await_status(client, job_id)


def test_bw5_long_range_note_on_the_job(ctx, signed_in):
    body = _bw_search(ctx, signed_in, "2019-01-01", "2020-12-31",
                      ["europepmc", "biorxiv_medrxiv"])
    assert len(body["notes"]) == 1
    assert "not read directly" in body["notes"][0]
    assert "Tick Europe PMC" not in body["notes"][0]


def test_bw5_note_says_to_tick_europe_pmc(ctx, signed_in):
    body = _bw_search(ctx, signed_in, "2019-01-01", "2020-12-31", ["biorxiv_medrxiv"])
    assert body["notes"][0].endswith("Tick Europe PMC to include them.")


def test_bw5_no_note_without_biorxiv(ctx, signed_in):
    body = _bw_search(ctx, signed_in, "2019-01-01", "2020-12-31", ["europepmc"])
    assert body["notes"] == []


def test_bw5_short_recent_range_names_the_limit(ctx, signed_in):
    from datetime import date, timedelta
    end = date.today()
    body = _bw_search(ctx, signed_in, (end - timedelta(days=7)).isoformat(), end.isoformat(),
                      ["europepmc", "biorxiv_medrxiv"])
    assert len(body["notes"]) == 1 and "every paper in the range is read" in body["notes"][0]


# ── TD7: one date window per search ───────────────────────────────────────────

def test_td7_dates_fixed_once(ctx, signed_in):
    """The orchestrator gets explicit start/end dates, the same ones the note
    was built from, even for a days_back filter."""
    from datetime import date, timedelta
    ctx.orchestrator = _fake_orchestrator(records=[])
    ctx.orchestrator.resolve_active_sources = lambda selection: ["biorxiv_medrxiv"]
    job_id = signed_in.post("/api/searches", json={"filter": {**FILTER, "days_back": 30}}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    fd = ctx.orchestrator.search.call_args.kwargs["filter_dict"]
    today = date.today()
    assert fd["end_date"] == today.isoformat()
    assert fd["start_date"] == (today - timedelta(days=30)).isoformat()
    assert "last 21 days" in body["notes"][0]          # 30 days > 21: the note used these dates


# ── WS1 / FL: limits and the incomplete-results summary ──────────────────────
# docs/implementation_plan_2026-10-08_limits_and_warnings.md

def _limited_orchestrator(limits, failures):
    """A fake that reports truncated sources through the real callbacks and
    records the max_results it was given."""
    orch = MagicMock()
    orch.seen_max = []

    def search(filter_dict=None, source_selection=None, max_results=200, **kw):
        orch.seen_max.append(max_results)
        for name, kind in failures:
            kw["on_source_failure"](name, kind)
        for name, read, total in limits:
            kw["on_source_limit"](name, read, total, False)
        return []
    orch.search.side_effect = search
    orch.resolve_active_sources = lambda sel: ["europepmc", "pubmed", "arxiv"]
    return orch


def test_ws1_limits_on_the_job(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator(
        [("europepmc", 200, 2391), ("pubmed", 200, 1204)],
        [("europepmc", "truncated"), ("pubmed", "truncated")])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    s = body["limit_summary"]
    assert s["heading"] == "Results are incomplete — 2 sources could not be read in full."
    rows = {r["source"]: r for r in s["rows"]}
    assert rows["europepmc"]["counts"] == "read 200 of 2,391 matches"
    assert rows["pubmed"]["action"] == "Part of Europe PMC — untick it."
    assert body["max_results"] == 200 and s["limit"] == 200


def test_ws1_no_summary_when_nothing_was_cut(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator([], [])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    assert _await_status(signed_in, job_id)["limit_summary"] is None


def test_fl2_saved_filter_uses_its_limit(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator([], [])
    fid = signed_in.post("/api/filters", json={"name": "Limited",
                                               "filter": {**FILTER, "max_results": 750}}).json()["id"]
    # The ad hoc box's value (sent by an old page) must not win.
    job_id = signed_in.post("/api/searches", json={"filter_id": fid, "max_results": 50}).json()["job_id"]
    _await_status(signed_in, job_id)
    assert ctx.orchestrator.seen_max == [750]


def test_fl2_old_filter_without_a_limit_uses_the_default(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator([], [])
    fid = signed_in.post("/api/filters", json={"name": "Old", "filter": FILTER}).json()["id"]
    job_id = signed_in.post("/api/searches", json={"filter_id": fid}).json()["job_id"]
    _await_status(signed_in, job_id)
    assert ctx.orchestrator.seen_max == [200]


def test_fl2_ad_hoc_uses_the_box(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator([], [])
    job_id = signed_in.post("/api/searches", json={"filter": FILTER, "max_results": 1500}).json()["job_id"]
    _await_status(signed_in, job_id)
    assert ctx.orchestrator.seen_max == [1500]
    assert signed_in.post("/api/searches", json={"filter": FILTER, "max_results": 2001}).status_code == 400


def test_fl3_filter_test_uses_its_limit(ctx, signed_in):
    ctx.orchestrator = _limited_orchestrator([], [])
    fid = signed_in.post("/api/filters", json={"name": "T",
                                               "filter": {**FILTER, "max_results": 333}}).json()["id"]
    job_id = signed_in.post(f"/api/filters/{fid}/test").json()["job_id"]
    _await_status(signed_in, job_id)
    assert ctx.orchestrator.seen_max == [333]


@pytest.mark.parametrize("bad", [0, 2001, "lots", 1.5, True])
def test_fl1_bad_limit_refused_on_save(ctx, signed_in, bad):
    r = signed_in.post("/api/filters", json={"name": f"Bad {bad}", "filter": {**FILTER, "max_results": bad}})
    assert r.status_code == 400 and "from 1 to 2,000" in r.json()["detail"]


def test_fl1_limit_saved_and_read_back(ctx, signed_in):
    fid = signed_in.post("/api/filters", json={"name": "Keep", "filter": {**FILTER, "max_results": "600"}}).json()["id"]
    got = [f for f in signed_in.get("/api/filters").json()["filters"] if f["id"] == fid][0]
    stored = got.get("filter", got)
    assert stored["max_results"] == 600
    r = signed_in.put(f"/api/filters/{fid}", json={"name": "Keep", "filter": {**FILTER, "max_results": 2001}})
    assert r.status_code == 400


def test_fl5_limits_from_config(ctx, signed_in):
    from web.routes_searches import search_limits
    assert search_limits(ctx.sources_config) == (200, 2000)
    assert search_limits({"search": {"default_max_results": 50, "max_results_ceiling": 400}}) == (50, 400)
    cfg = signed_in.get("/api/config").json()
    assert cfg["search_limits"] == {"default": 200, "ceiling": 2000}


def test_ws1_summary_shown_while_the_search_runs(ctx, signed_in):
    """The summary is on the job as soon as a source reports, not only after
    the whole search (and enrichment) ends — seen in the local browser check."""
    import threading
    release = threading.Event()
    orch = _limited_orchestrator([("europepmc", 200, 929)], [("europepmc", "truncated")])
    inner = orch.search.side_effect

    def slow(**kw):
        inner(**kw)
        release.wait(5)            # the rest of the search / enrichment
        return []
    orch.search.side_effect = slow
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={"filter": FILTER}).json()["job_id"]
    try:
        for _ in range(200):
            body = signed_in.get(f"/api/searches/{job_id}").json()
            if body.get("limit_summary"):
                break
            time.sleep(0.01)
        assert body["status"] == "running"
        assert body["limit_summary"]["rows"][0]["counts"] == "read 200 of 929 matches"
    finally:
        release.set()
    _await_status(signed_in, job_id)


# ── DW / AY: the date window searched, and All years ─────────────────────────
# docs/implementation_plan_2026-10-08_date_window.md

def test_dw1_window_on_the_job(ctx, signed_in):
    """The job carries the window fixed_dates set: days_back, From/To, All years."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    cases = [
        ({**FILTER, "days_back": 30},
         {"start": (date.today() - timedelta(days=30)).isoformat(), "end": today, "all_years": False}),
        ({**FILTER, "start_date": "2020-01-01", "end_date": "2020-12-31"},
         {"start": "2020-01-01", "end": "2020-12-31", "all_years": False}),
        ({**FILTER, "all_years": True},
         {"start": "1900-01-01", "end": today, "all_years": True}),
    ]
    for filter_dict, window in cases:
        ctx.orchestrator = _fake_orchestrator(records=[])
        job_id = signed_in.post("/api/searches", json={"filter": filter_dict}).json()["job_id"]
        assert _await_status(signed_in, job_id)["date_window"] == window


def test_ay6_all_sources_get_the_window(ctx, signed_in):
    """With the real orchestrator, every source's request carries the All
    years start date; bioRxiv/medRxiv gets its last-21-days note."""
    from src.sources.orchestrator import SourceOrchestrator
    seen = {}

    def recorder(name):
        class _Recorder:
            last_page_size = 0
            last_total = 0

            def search(self, query, page=1, page_size=50, filter_dict=None, **_):
                seen[name] = (query, (filter_dict or {}).get("start_date"))
                return []

            def normalize(self, raw):
                raise NotImplementedError
        return _Recorder()

    names = ("europepmc", "pubmed", "arxiv", "psyarxiv", "socarxiv", "biorxiv_medrxiv")
    orch = SourceOrchestrator(ctx.sources_config)
    orch._search_adapters = {n: recorder(n) for n in names}
    ctx.orchestrator = orch
    job_id = signed_in.post("/api/searches", json={
        "filter": {**FILTER, "all_years": True},
        "source_selection": {"all": False, "selected": list(names)}}).json()["job_id"]
    body = _await_status(signed_in, job_id)
    assert set(seen) == set(names)
    assert "FIRST_PDATE:[1900-01-01 TO" in seen["europepmc"][0]
    assert "FIRST_PDATE:[1900-01-01 TO" in seen["pubmed"][0]
    assert "submittedDate:[190001010000 TO" in seen["arxiv"][0]
    for name in ("arxiv", "psyarxiv", "socarxiv", "biorxiv_medrxiv"):
        assert seen[name][1] == "1900-01-01", name
    assert any("last 21 days" in n for n in body["notes"])


def test_dw4_hint_in_config_endpoint(signed_in):
    hint = signed_in.get("/api/config").json()["date_window_hint"]
    assert hint.startswith("Dates are when a paper first appeared")


@pytest.mark.parametrize("bad", ["true", 1, 0, None, "yes"])
def test_ay4_all_years_must_be_a_bool(ctx, signed_in, bad):
    r = signed_in.post("/api/filters", json={"name": f"AY {bad}", "filter": {**FILTER, "all_years": bad}})
    assert r.status_code == 400 and "All years" in r.json()["detail"]


def test_ay4_all_years_saved_and_read_back(ctx, signed_in):
    fid = signed_in.post("/api/filters", json={"name": "Ever",
                                               "filter": {**FILTER, "all_years": True}}).json()["id"]
    got = [f for f in signed_in.get("/api/filters").json()["filters"] if f["id"] == fid][0]
    assert got.get("filter", got)["all_years"] is True
    r = signed_in.put(f"/api/filters/{fid}", json={"name": "Ever", "filter": {**FILTER, "all_years": False}})
    assert r.status_code == 200
    assert "all_years" not in r.json().get("filter", r.json())     # false is not stored
    # Running the saved filter uses the wide window.
    signed_in.put(f"/api/filters/{fid}", json={"name": "Ever", "filter": {**FILTER, "all_years": True}})
    ctx.orchestrator = _fake_orchestrator(records=[])
    job_id = signed_in.post(f"/api/filters/{fid}/test").json()["job_id"]
    assert _await_status(signed_in, job_id)["date_window"]["all_years"] is True


# ── KW9: a keyword-only paper survives the route ─────────────────────────────
# docs/implementation_plan_2026-10-08_author_keywords.md#KW9

def _keyword_only_record():
    r = _record("Approaches to ketamine-assisted couple therapy", abstract="Couples and ketamine.")
    r.keywords = ["Ketamine", "Internal Family Systems Therapy"]
    return r


def test_kw9_keyword_only_paper_kept(ctx, signed_in):
    ifs = {**FILTER, "text_groups": [{"title": "", "abstract": "", "both": "internal family systems"}]}
    ctx.orchestrator = _fake_orchestrator(records=[_keyword_only_record()])
    job_id = signed_in.post("/api/searches", json={"filter": ifs}).json()["job_id"]
    _await_status(signed_in, job_id)
    page = signed_in.get(f"/api/searches/{job_id}/results").json()
    assert [p["title"] for p in page["results"]] == ["Approaches to ketamine-assisted couple therapy"]
    assert page["results"][0]["keywords"] == ["Ketamine", "Internal Family Systems Therapy"]
    # Search within uses keywords too.
    page = signed_in.get(f"/api/searches/{job_id}/results", params={"within": "ketamine AND therapy"}).json()
    assert page["total"] == 1
    page = signed_in.get(f"/api/searches/{job_id}/results", params={"within": "family therapy"}).json()
    assert page["total"] == 0                            # not a phrase in any one field
    # A saved filter run keeps it as well.
    fid = signed_in.post("/api/filters", json={"name": "IFS", "filter": ifs}).json()["id"]
    ctx.orchestrator = _fake_orchestrator(records=[_keyword_only_record()])
    job_id = signed_in.post(f"/api/filters/{fid}/test").json()["job_id"]
    assert _await_status(signed_in, job_id)["matched"] == 1


# ── TG1: a broken numeric setting does not stop searches ─────────────────────
# docs/implementation_plan_2026-10-08_gate_todos.md#TG1

def test_tg1_search_starts_with_a_broken_config(ctx, signed_in):
    """A blank setting must not make searches fail (the 2026-10-08 finding)."""
    ctx.sources_config = {**ctx.sources_config,
                          "search": {"default_max_results": None, "max_results_ceiling": ""},
                          "osf": {"max_title_terms": "lots"}}
    ctx.orchestrator = _fake_orchestrator(records=[])
    r = signed_in.post("/api/searches", json={"filter": FILTER})
    assert r.status_code == 202
    assert _await_status(signed_in, r.json()["job_id"])["status"] == "done"
