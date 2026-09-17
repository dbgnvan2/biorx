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

from src import user_store

from .auth import current_user, get_context
from .deps import AppContext
from .routes_searches import SearchRequest, _run_search

router = APIRouter()


class FilterBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    filter: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/filters")
def list_filters(ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    return {"filters": user_store.list_filters(ctx.db, user_id)}


@router.post("/api/filters", status_code=status.HTTP_201_CREATED)
def create_filter(body: FilterBody,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
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
    selection = filter_dict.get("source_selection", {"all": True, "selected": []})
    from .routes_searches import DEFAULT_MAX_RESULTS
    job = ctx.jobs.submit(
        "filter_test", user_id,
        _run_search(ctx, filter_dict, selection, DEFAULT_MAX_RESULTS),
    )
    return job.to_dict()
