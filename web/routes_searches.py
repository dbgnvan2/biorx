"""
Purpose: Run a multi-source search as a background job and page its results.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, #2.3
Tests:   tests/web/test_searches_routes.py

A search is a job, not a request: eight sources, up to twenty pages each, arXiv
spaced three seconds apart, then per-record enrichment. Holding an HTTP
connection open for that pins a worker and is cut off by the platform proxy
long before the search finishes.

The orchestrator is wrapped, not modified: this passes its existing on_batch /
on_progress / on_status / should_stop callbacks straight through.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import user_store
from src.filtering import filter_papers, normalise_filter, without_license
from src.filters_store import EMPTY_FILTER_MESSAGE, filter_has_text
from src.jobs import Job, JobLookup

from .auth import current_user, get_context
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()



_FALLBACK_EXPLANATION = "could not be reached"


def failure_reason(kind: str, sources_config: Dict[str, Any]) -> str:
    """Plain-language reason for a failure kind, from sources_config.yaml
    failure_explanations (D2)."""
    table = (sources_config or {}).get("failure_explanations") or {}
    return str(table.get(kind) or _FALLBACK_EXPLANATION)


def record_failure(job: Job, source_name: str, kind: str,
                   sources_config: Dict[str, Any]) -> None:
    """Record a failed source and why, once per source.

    Purpose: Show which sources failed, from the orchestrator's structured
             report rather than by parsing its status text (review S2).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S2
    Tests:   tests/web/test_searches_routes.py::test_d2_job_records_label_and_reason_once
    """
    from src.sources.config import source_label
    if source_name not in job.sources_failed:
        job.sources_failed.append(source_name)
    job.source_problems.setdefault(source_label(source_name),
                                   failure_reason(kind, sources_config))


# Job kinds whose result is a list of papers (a search, or a filter's test run).
SEARCH_JOB_KIND = "search"
FILTER_TEST_JOB_KIND = "filter_test"
SEARCH_JOB_KINDS = (SEARCH_JOB_KIND, FILTER_TEST_JOB_KIND)

MAX_RESULTS_CEILING = 2000
DEFAULT_MAX_RESULTS = 200
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def refuse_empty_filter(filter_dict: Dict[str, Any]) -> None:
    """Purpose: Refuse to queue a run for a filter with nothing to search for.
    Spec:    docs/implementation_plan_2026-09-18_filter_run.md#FR1
    Tests:   tests/web/test_searches_routes.py::test_fr1_1_empty_saved_filter_is_refused,
             tests/web/test_filter_test_route.py::test_fr1_3_empty_filter_test_is_refused

    Called by every route that starts a search job, before the job is queued.
    """
    if not filter_has_text(filter_dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=EMPTY_FILTER_MESSAGE)


class SearchRequest(BaseModel):
    filter_id: Optional[int] = None
    filter: Optional[Dict[str, Any]] = None
    source_selection: Optional[Dict[str, Any]] = None
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_CEILING)


def _run_search(ctx: AppContext, filter_dict: Dict[str, Any],
                source_selection: Dict[str, Any], max_results: int):
    """Build the callable the job runner executes.

    Returns matched papers, built after enrichment so they include it.
    Spec:  docs/implementation_plan_2026-09-18_filter_run.md#C2, #C3
    Tests: tests/web/test_searches_routes.py::test_fr2_3_results_include_enriched_fields,
           tests/web/test_searches_routes.py::test_fr2_4_no_match_means_no_enrichment,
           tests/web/test_searches_routes.py::test_fr3_1_fetched_survives_enrichment

    Every failure the orchestrator swallows per source
    is recorded on the job, so an empty result set is never mistaken for a quiet
    week when a source was simply unreachable (learnings P2).
    """
    # The query builders read the canonical shape too, not only filter_papers.
    filter_dict = normalise_filter(filter_dict)
    # Enrichment supplies the licence for many papers, so the licence is
    # checked once enrichment has run, not as pages arrive (review B5).
    pre_enrichment = without_license(filter_dict)

    def work(job: Job) -> List[Dict[str, Any]]:
        # Matched records are kept as objects and turned into dicts only after
        # the search returns, so the results carry what enrichment and later
        # duplicate merges added. Snapshotting each page on arrival threw that
        # away (plan 2026-09-18 D-b; learnings P36).
        matched: List[Any] = []
        matched_ids: set = set()

        def on_batch(records):
            papers = [r.to_dict() for r in records]
            kept = {id(p) for p in filter_papers(papers, pre_enrichment, normalised=True)}
            for record, paper in zip(records, papers):
                if id(paper) in kept:
                    matched.append(record)
                    matched_ids.add(id(record))
            job.matched = len(matched)

        def on_progress(fetched: int, total: int):
            job.fetched = fetched
            job.total = max(fetched, total)

        def on_enrich_progress(done: int, total: int):
            job.enriched = done
            job.enrich_total = total

        def on_enrich_problem(label: str, failed: int, attempted: int):
            job.enrich_problems[label] = [failed, attempted]

        def on_status(message: str):
            job.phase = message

        def on_source_failure(source_name: str, kind: str):
            record_failure(job, source_name, kind, ctx.sources_config)

        try:
            ctx.get_orchestrator().search(
                filter_dict=filter_dict,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=on_progress,
                on_status=on_status,
                should_stop=job.should_stop,
                max_results=max_results,
                # Only papers that passed the filter are worth two HTTP calls.
                enrich_only=lambda r: id(r) in matched_ids,
                on_enrich_progress=on_enrich_progress,
                on_enrich_problem=on_enrich_problem,
                on_source_failure=on_source_failure,
            )
        finally:
            # This job ran on a pool thread that took a database connection.
            ctx.db.release()
        results = filter_papers([r.to_dict() for r in matched], filter_dict)
        job.matched = len(results)
        return results

    return work


@router.post("/api/searches", status_code=status.HTTP_202_ACCEPTED)
def start_search(body: SearchRequest,
                 ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    """Queue a search. Returns a job id to poll."""
    if body.filter_id is not None:
        filter_dict = user_store.get_filter(ctx.db, user_id, body.filter_id)
        if filter_dict is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="No such filter.")
    elif body.filter is not None:
        filter_dict = body.filter
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Provide either filter_id or filter.")
    refuse_empty_filter(filter_dict)

    selection = body.source_selection or filter_dict.get(
        "source_selection", {"all": True, "selected": []}
    )
    return submit_search(ctx, SEARCH_JOB_KIND, user_id,
                         _run_search(ctx, filter_dict, selection, body.max_results))


# Searches (and filter tests) share one key per user: each can hold a worker
# for minutes, so one user may run one at a time (review A14).
SEARCH_KEY = ("search",)


def submit_search(ctx: AppContext, kind: str, user_id: str, work):
    """Purpose: Queue a search, or answer 409 while this user's last one runs.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A14
    Tests:   tests/web/test_jobs.py::test_a14_one_search_per_user
    """
    from fastapi.responses import JSONResponse
    from src.jobs import JobAlreadyRunning
    try:
        job = ctx.jobs.submit(kind, user_id, work, key=SEARCH_KEY)
    except JobAlreadyRunning as e:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
            "detail": "A search is already running — wait for it to finish or stop it first.",
            "job_id": e.job.id})
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The server is restarting — try again in a minute.") from e
    return job.to_dict()


@router.get("/api/jobs/running")
def running_jobs(ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    """Purpose: This user's queued or running jobs, so a reloaded page can show
             which buttons are still busy and resume polling (review A7).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A7
    Tests:   tests/web/test_jobs.py::test_a7_running_jobs_listed
    """
    out = []
    for job, key in ctx.jobs.running(user_id):
        entry = {"job_id": job.id, "kind": job.kind, "status": job.status,
                 "phase": job.phase}
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "paper":
            entry["paper"] = key[1]
        elif isinstance(key, tuple) and len(key) == 2 and key[0] == "review":
            entry["list_id"] = key[1]
        out.append(entry)
    return {"jobs": out}


def _job_or_404(ctx: AppContext, job_id: str, user_id: str) -> Job:
    """Fetch a job, distinguishing "expired" from "never existed".

    A job that aged out should tell the user to run it again; collapsing that
    into "not found" leaves them guessing.
    """
    job, reason = ctx.jobs.lookup(job_id, user_id)
    if reason == JobLookup.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That search has expired — run it again.",
        )
    # Only search-shaped jobs: a discover or summary job has a different
    # result shape, and slicing it here would be a 500.
    if job is None or job.kind not in SEARCH_JOB_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such job.")
    return job


@router.get("/api/searches/{job_id}")
def search_status(job_id: str,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    return _job_or_404(ctx, job_id, user_id).to_dict()


@router.get("/api/searches/{job_id}/results")
def search_results(job_id: str, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """A page of matched papers. Empty until the job is done: the result is set
    when the job finishes."""
    job = _job_or_404(ctx, job_id, user_id)
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    results = job.result or []
    return {
        "job_id": job.id,
        "status": job.status,
        "total": len(results),
        "offset": offset,
        "limit": limit,
        "results": results[offset:offset + limit],
        "sources_failed": list(job.sources_failed),
    }


@router.delete("/api/searches/{job_id}")
def cancel_search(job_id: str,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    _job_or_404(ctx, job_id, user_id)
    cancelled = ctx.jobs.cancel(job_id, user_id)
    return {"ok": True, "cancelled": cancelled}


class SaveAsListBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    paper_ids: Optional[List[str]] = None   # canonical_ids; None means save all


@router.post("/api/searches/{job_id}/save-as-list", status_code=status.HTTP_201_CREATED)
def save_search_as_list(job_id: str, body: SaveAsListBody,
                        ctx: AppContext = Depends(get_context),
                        user_id: str = Depends(current_user)):
    """Save a completed search (or a subset) as a new reference list.

    paper_ids, when provided, is a list of canonical_id strings from the
    search results — the caller sends only the papers the user checked.
    Returns the list plus how many papers were saved and which were skipped,
    so a paper that could not be stored is never dropped silently (P2).
    """
    job = _job_or_404(ctx, job_id, user_id)
    if job.status != "done":
        # job.result is only set when the job finishes; saving earlier would
        # create an empty list and take the name.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The search has not finished yet.")

    results: List[Dict[str, Any]] = job.result or []
    if body.paper_ids is not None:
        selected_ids = set(body.paper_ids)
        results = [p for p in results
                   if p.get("canonical_id") in selected_ids or p.get("doi") in selected_ids]

    db_ids: List[int] = []
    skipped: List[str] = []
    for paper in results:
        try:
            pid = ctx.db.insert_paper(paper)
        except sqlite3.IntegrityError:
            # Not a duplicate (insert_paper returns None for those, M27): the
            # row could not be stored. Reported with the others skipped below.
            pid = None
            skipped.append(paper.get("title") or paper.get("canonical_id") or "(untitled)")
            continue
        if not pid:
            existing = ctx.db.find_paper(paper)
            pid = existing["id"] if existing else None
        if pid:
            db_ids.append(pid)
        else:
            skipped.append(paper.get("title") or paper.get("canonical_id") or "(untitled)")
    if skipped:
        logger.warning("save-as-list: %d of %d papers could not be stored",
                       len(skipped), len(results))

    try:
        list_id = user_store.create_reference_list_with_papers(
            ctx.db, user_id, body.name, db_ids)
    except user_store.DuplicateListName:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"You already have a list called {body.name!r}.")
    payload = user_store.get_reference_list(ctx.db, user_id, list_id)
    payload.update({"saved": len(set(db_ids)), "requested": len(results), "skipped": skipped})
    return payload


def summary_card(paper: Dict[str, Any], s: Dict[str, Any], **extra) -> Dict[str, Any]:
    """Purpose: The one shape a stored summary is sent to the page in, for
             search results and saved lists alike (review M11).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M11
    Tests:   tests/web/test_searches_routes.py::test_m11_card_identical_across_endpoints
    """
    card = {
        "canonical_id": paper.get("canonical_id") or "",
        "doi": paper.get("doi") or "",
        "title": paper.get("title") or "",
        "paper_id": paper.get("paper_id"),
        "key_findings": s.get("key_findings") or [],
        "methodology": s.get("methodology") or "",
        "conclusions": s.get("conclusions") or "",
        "model_version": s.get("model_version") or "",
        "source_text": s.get("source_text") or "",
        "text_source": s.get("text_source") or "",
        "abstract_only": s.get("summary_text") if s.get("source_text") == "abstract" else "",
        "created_at": str(s.get("created_at") or ""),
    }
    card.update(extra)
    return card


# ── Summaries for a search's results (S1–S3, docs/implementation_plan_2026-09-18_cache_and_summaries.md)

def _job_summaries(ctx: AppContext, job: Job, only_ids: Optional[List[str]] = None):
    """(items, summaries) for a finished search: every result as an item, and
    the stored summary of each one that has one, keyed by paper row id.

    Read-only: a result that was never stored has no row and so no summary;
    nothing is inserted here.
    """
    results: List[Dict[str, Any]] = job.result or []
    if only_ids is not None:
        wanted = set(only_ids)
        results = [p for p in results
                   if p.get("canonical_id") in wanted or p.get("doi") in wanted]
    items: List[Dict[str, Any]] = []
    seen = set()
    # Bulk lookups (review M11): per-paper queries were ~3 per result, so a
    # 2000-result search made ~6000 queries on every call.
    for paper, pid in zip(results, ctx.db.find_paper_ids(results)):
        if pid is not None:
            if pid in seen:
                continue      # two results for the same paper: list it once
            seen.add(pid)
        items.append({"paper": {**paper, "paper_id": pid}})
    summaries = ctx.db.summaries_for_papers(
        i["paper"]["paper_id"] for i in items if i["paper"]["paper_id"] is not None)
    return items, summaries


def _finished_search(ctx: AppContext, job_id: str, user_id: str) -> Job:
    job = _job_or_404(ctx, job_id, user_id)
    if job.status != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The search has not finished yet.")
    return job


@router.get("/api/searches/{job_id}/summaries")
def search_summaries(job_id: str,
                     ctx: AppContext = Depends(get_context),
                     user_id: str = Depends(current_user)):
    """Stored summaries for this search's results, newest first (S1, S2)."""
    job = _finished_search(ctx, job_id, user_id)
    items, summaries = _job_summaries(ctx, job)
    out = [summary_card(item["paper"], summaries[item["paper"]["paper_id"]])
           for item in items        # one item per paper (_job_summaries dedupes)
           if item["paper"]["paper_id"] in summaries]
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return {"summaries": out, "total_results": len(items)}


class SummariesPdfBody(BaseModel):
    title: str = Field(default="", max_length=200)
    paper_ids: Optional[List[str]] = None     # canonical_ids; None = all results


@router.post("/api/searches/{job_id}/summaries.pdf")
def search_summaries_pdf(job_id: str, body: SummariesPdfBody,
                         ctx: AppContext = Depends(get_context),
                         user_id: str = Depends(current_user)):
    """The summaries for this search's results (ticked ones if given) as one
    PDF, in the Saved References layout (S3)."""
    from fastapi import Response
    from src.summary_pdf import build_summaries_pdf

    job = _finished_search(ctx, job_id, user_id)
    items, summaries = _job_summaries(ctx, job, body.paper_ids)
    title = body.title.strip() or "Search results"
    data = build_summaries_pdf(title, items, summaries)
    from src.reference_export import safe_filename
    safe = safe_filename(title)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{safe} - summaries.pdf"'})
