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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import user_store
from src.filtering import filter_papers, normalise_filter
from src.jobs import Job, JobLookup

from .auth import current_user, get_context
from .deps import AppContext
from src.sources.orchestrator import FAILURE_STATUS_MARKER

logger = logging.getLogger(__name__)
router = APIRouter()

# FAILURE_STATUS_MARKER is imported from orchestrator (P19: single source of truth;
# wording change there breaks this import and the round-trip test, not silently).


def source_from_failure_status(message: str) -> str:
    """Return the source name a failure status refers to, or "".

    Matches the source's display label rather than its internal name, because
    that is what the status string carries.
    """
    if FAILURE_STATUS_MARKER not in message:
        return ""
    from src.sources.orchestrator import _SOURCE_LABELS
    for name, label in _SOURCE_LABELS.items():
        if message.startswith(label):
            return name
    return ""


# Job kinds whose result is a list of papers (a search, or a filter's test run).
SEARCH_JOB_KIND = "search"
FILTER_TEST_JOB_KIND = "filter_test"
SEARCH_JOB_KINDS = (SEARCH_JOB_KIND, FILTER_TEST_JOB_KIND)

MAX_RESULTS_CEILING = 2000
DEFAULT_MAX_RESULTS = 200
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class SearchRequest(BaseModel):
    filter_id: Optional[int] = None
    filter: Optional[Dict[str, Any]] = None
    source_selection: Optional[Dict[str, Any]] = None
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_CEILING)


def _run_search(ctx: AppContext, filter_dict: Dict[str, Any],
                source_selection: Dict[str, Any], max_results: int):
    """Build the callable the job runner executes.

    Returns matched papers. Every failure the orchestrator swallows per source
    is recorded on the job, so an empty result set is never mistaken for a quiet
    week when a source was simply unreachable (learnings P2).
    """
    # The query builders read the canonical shape too, not only filter_papers.
    filter_dict = normalise_filter(filter_dict)

    def work(job: Job) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []

        def on_batch(records):
            papers = [r.to_dict() for r in records]
            keep = filter_papers(papers, filter_dict)
            matched.extend(keep)
            job.matched = len(matched)

        def on_progress(fetched: int, total: int):
            job.fetched = fetched
            job.total = max(fetched, total)

        def on_status(message: str):
            job.phase = message
            failed = source_from_failure_status(message)
            if failed and failed not in job.sources_failed:
                job.sources_failed.append(failed)

        try:
            ctx.get_orchestrator().search(
                filter_dict=filter_dict,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=on_progress,
                on_status=on_status,
                should_stop=job.should_stop,
                max_results=max_results,
            )
        finally:
            # This job ran on a pool thread that took a database connection.
            ctx.db.release()
        return matched

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

    selection = body.source_selection or filter_dict.get(
        "source_selection", {"all": True, "selected": []}
    )
    job = ctx.jobs.submit(
        SEARCH_JOB_KIND, user_id,
        _run_search(ctx, filter_dict, selection, body.max_results),
    )
    return job.to_dict()


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
        pid = ctx.db.insert_paper(paper)
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
