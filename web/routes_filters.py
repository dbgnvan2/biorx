"""
Purpose: A user's saved filters.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, W6
Tests:   tests/web/test_filters_routes.py

Filters are per user in SQLite rather than in a shared filters.json: a single mutable file in a container is last-write-wins
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
                              refuse_empty_filter, submit_search)

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


def _refuse_name_clash(ctx: AppContext, user_id: str, name: str,
                       exclude_id: int = None) -> None:
    """Purpose: A save never overwrites another filter by taking its name.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A2
    Tests:   tests/web/test_filters_routes.py::test_a2_rename_onto_existing_is_409,
             tests/web/test_filters_routes.py::test_a2_create_duplicate_is_409
    """
    if user_store.filter_name_taken(ctx.db, user_id, name, exclude_id=exclude_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f'A filter called "{name.strip()}" already exists. '
                                   "Choose another name.")


@router.get("/api/vocabulary")
def get_vocabulary(user_id: str = Depends(current_user)):
    """Purpose: The options for each filter facet, for the page's dropdowns.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S3
    Tests:   tests/web/test_filters_routes.py::test_s3_vocabulary_served
    """
    return filter_vocabulary.for_client()


def _check_filter_limit(filter_dict: Dict[str, Any], ctx: AppContext) -> None:
    """FL1: a saved limit is a whole number within 1..ceiling (400 otherwise);
    stored as an int, or left out to use the default."""
    from .routes_searches import check_limit
    n = check_limit(filter_dict.get("max_results"), ctx.sources_config)
    if n is None:
        filter_dict.pop("max_results", None)
    else:
        filter_dict["max_results"] = n


def _check_all_years(filter_dict: Dict[str, Any]) -> None:
    """AY4: All years is stored as a real bool (400 for anything else), so
    only a real true widens the window (fixed_dates checks `is True`)."""
    if "all_years" not in filter_dict:
        return
    if not isinstance(filter_dict["all_years"], bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="All years must be true or false.")
    if not filter_dict["all_years"]:
        filter_dict.pop("all_years")


@router.get("/api/filters")
def list_filters(ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    return {"filters": user_store.list_filters(ctx.db, user_id)}


@router.post("/api/filters", status_code=status.HTTP_201_CREATED)
def create_filter(body: FilterBody,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    refuse_unusable_facets(body.filter)
    _check_filter_limit(body.filter, ctx)
    _check_all_years(body.filter)
    _refuse_name_clash(ctx, user_id, body.name)
    filter_id = user_store.insert_filter(
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
    _check_filter_limit(body.filter, ctx)
    _check_all_years(body.filter)
    _refuse_name_clash(ctx, user_id, body.name, exclude_id=filter_id)
    # Updated in place: a rename keeps the filter's id.
    user_store.update_filter(ctx.db, user_id, filter_id, body.name, body.filter,
                             body.enabled)
    return user_store.get_filter(ctx.db, user_id, filter_id)


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
    from src.search_limits import filter_limit
    return submit_search(ctx, FILTER_TEST_JOB_KIND, user_id,          # FL3: its own limit
                         _run_search(ctx, filter_dict, selection,
                                     filter_limit(filter_dict, ctx.sources_config)))
