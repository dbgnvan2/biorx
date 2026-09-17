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
from src.filtering import filter_papers
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
        "search", user_id,
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
    if job is None:
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
    """A page of matched papers. Available while the job is still running."""
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
