"""
Purpose: A user's saved filters.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, W6
Tests:   tests/web/test_filters_routes.py

Filters are per user in SQLite rather than in the shared filters.json the
desktop app edits: a single mutable file in a container is last-write-wins
between colleagues.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import filter_vocabulary, user_store

from .auth import current_user, get_context
from .deps import AppContext
from .routes_searches import (FILTER_TEST_JOB_KIND, SearchRequest, _run_search,
                              refuse_empty_filter)

router = APIRouter()


class FilterBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    filter: Dict[str, Any] = Field(default_factory=dict)


def refuse_unusable_facets(filter_dict: Dict[str, Any]) -> None:
    """Purpose: Refuse a filter whose facets the search cannot apply.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S3, #B5
    Tests:   tests/web/test_filters_routes.py::test_s3_unknown_facet_value_refused,
             tests/web/test_filters_routes.py::test_b5_institution_refused

    An unknown value used to fall through as "no restriction", so the filter
    silently matched more than it said; an institution matched nothing.
    """
    problems = filter_vocabulary.problems(filter_dict)
    if problems:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This filter cannot be saved: " + "; ".join(problems))


@router.get("/api/vocabulary")
def get_vocabulary(user_id: str = Depends(current_user)):
    """Purpose: The options for each filter facet, for the page's dropdowns.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S3
    Tests:   tests/web/test_filters_routes.py::test_s3_vocabulary_served
    """
    return filter_vocabulary.for_client()


@router.get("/api/filters")
def list_filters(ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    return {"filters": user_store.list_filters(ctx.db, user_id)}


@router.post("/api/filters", status_code=status.HTTP_201_CREATED)
def create_filter(body: FilterBody,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    refuse_unusable_facets(body.filter)
    filter_id = user_store.upsert_filter(
        ctx.db, user_id, body.name, body.filter, body.enabled
    )
    return user_store.get_filter(ctx.db, user_id, filter_id)


@router.put("/api/filters/{filter_id}")
def update_filter(filter_id: int, body: FilterBody,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    existing = user_store.get_filter(ctx.db, user_id, filter_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such filter.")
    refuse_unusable_facets(body.filter)
    # A rename is an upsert on the new name; delete the old row so a rename
    # does not silently leave two copies.
    new_id = user_store.upsert_filter(
        ctx.db, user_id, body.name, body.filter, body.enabled
    )
    if new_id != filter_id:
        user_store.delete_filter(ctx.db, user_id, filter_id)
    return user_store.get_filter(ctx.db, user_id, new_id)


@router.delete("/api/filters/{filter_id}")
def delete_filter(filter_id: int,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    if not user_store.delete_filter(ctx.db, user_id, filter_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such filter.")
    return {"ok": True}


@router.post("/api/filters/{filter_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_filter(filter_id: int,
                ctx: AppContext = Depends(get_context),
                user_id: str = Depends(current_user)):
    """Run a filter as a search job. Returns job_id; poll /api/searches/{job_id}."""
    filter_dict = user_store.get_filter(ctx.db, user_id, filter_id)
    if filter_dict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such filter.")
    refuse_empty_filter(filter_dict)
    selection = filter_dict.get("source_selection", {"all": True, "selected": []})
    from .routes_searches import DEFAULT_MAX_RESULTS
    job = ctx.jobs.submit(
        FILTER_TEST_JOB_KIND, user_id,
        _run_search(ctx, filter_dict, selection, DEFAULT_MAX_RESULTS),
    )
    return job.to_dict()
