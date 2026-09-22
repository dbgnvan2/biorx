"""
One synthesis across the papers ticked in a saved list.

Purpose: Run a cross-paper review as a background job, and store what it read.
Spec:    docs/implementation_plan_2026-09-20_references_batch.md#M4
Tests:   tests/web/test_review_routes.py

Admission goes through routes_summaries._resolve_for, the same gate a summary
uses, so the owner-key daily allowance covers a review exactly as it covers a
summary. A second admission path would be a second opinion on who may spend.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import review as review_builder
from src import user_store
from src.jobs import Job
from src.llm_providers import LLMError

from .auth import current_user, get_context
from .deps import AppContext
from .routes_references import _get_list_or_404, _parse_item_ids
from .routes_summaries import USAGE_KIND, _resolve_for

logger = logging.getLogger(__name__)

router = APIRouter()

JOB_KIND = "review"


class ReviewRequest(BaseModel):
    list_id: int
    item_ids: str = Field(default="", max_length=4000)
    # Inline key from localStorage — same pattern as summaries and discover.
    api_key: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=50)
    model: str = Field(default="", max_length=200)


def _max_prompt_chars(config: Dict[str, Any]) -> int:
    """The cap on the combined prompt, from config rather than a literal (P4)."""
    value = (config or {}).get("review_max_prompt_chars")
    if isinstance(value, int) and value > 0:
        return value
    return review_builder.DEFAULT_MAX_PROMPT_CHARS


def _gather_for(ctx: AppContext, list_id: int, item_ids: Optional[set]):
    """The papers to review, with their stored summaries."""
    items = user_store.list_reference_items(ctx.db, list_id)
    if item_ids is not None:
        items = [i for i in items if i.get("item_id") in item_ids]
    summaries = {}
    for item in items:
        paper_id = item["paper"]["paper_id"]
        row = ctx.db.get_summary(paper_id)
        if row:
            summaries[paper_id] = row
    return items, review_builder.gather(items, summaries)


def _run_review(ctx: AppContext, user_id: str, body: ReviewRequest, resolved,
                usage_id: Optional[int] = None):
    def work(job: Job) -> Dict[str, Any]:
        provider_called = False
        try:
            job.phase = "Reading the stored summaries"
            items, gathered = _gather_for(ctx, body.list_id,
                                          _parse_item_ids(body.item_ids or None))
            built = review_builder.build_prompt(
                gathered, _max_prompt_chars(ctx.llm_config))

            if not built["included"]:
                # Nothing to synthesize. Said plainly, and no model is called —
                # a review of nothing would be invention.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="None of these papers have a summary or an abstract "
                           "stored, so there is nothing to review yet.")

            job.total = len(built["included"])
            job.phase = f"Asking {resolved.provider} about {job.total} papers"
            provider_called = True
            text, usage = resolved.client.generate(
                built["prompt"], context=review_builder.SYSTEM_PROMPT)
            job.token_usage = usage

            if not (text or "").strip():
                raise LLMError(f"{resolved.provider} returned an empty review",
                               usage=usage)

            job.phase = "Saving"
            note = review_builder.basis_note(built["included"])
            left_out = list(built["dropped"]) + list(built["excluded"])
            review_id = user_store.save_review(
                ctx.db, user_id, body.list_id, text.strip(), note,
                built["included"], left_out, resolved.model)

            return {
                "review_id": review_id,
                "review_text": text.strip(),
                "basis_note": note,
                "contributors": built["included"],
                "left_out": left_out,
                "provider": resolved.provider,
                "model": resolved.model,
            }
        except BaseException as exc:
            # generate() raises carrying what the billed call cost; recover it
            # so a failed review is not recorded as costing nothing (the same
            # rule as summaries and discover).
            if not job.token_usage.counted:
                carried = getattr(exc, "usage", None)
                if carried is not None:
                    job.token_usage = carried
            raise
        finally:
            try:
                if provider_called:
                    user_store.record_spend(ctx.db, user_id, USAGE_KIND,
                                            resolved.provider, resolved.model,
                                            resolved.key_source, usage_id,
                                            job.token_usage)
                elif usage_id is not None:
                    user_store.release_usage(ctx.db, usage_id)
            finally:
                ctx.db.release()

    return work


@router.post("/api/reviews", status_code=status.HTTP_202_ACCEPTED)
def start_review(body: ReviewRequest,
                 ctx: AppContext = Depends(get_context),
                 user_id: str = Depends(current_user)):
    """Queue a cross-paper review of one saved list."""
    _get_list_or_404(ctx, user_id, body.list_id)
    resolved, usage_id = _resolve_for(ctx, user_id, body.api_key.strip(),
                                      body.provider, body.model)
    job = ctx.jobs.submit(JOB_KIND, user_id,
                          _run_review(ctx, user_id, body, resolved, usage_id))
    payload = job.to_dict()
    payload.update(provider=resolved.provider, model=resolved.model,
                   key_source=resolved.key_source)
    return payload


@router.get("/api/reviews/{job_id}")
def review_status(job_id: str,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    job, reason = ctx.jobs.lookup(job_id, user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=reason or "No such job.")
    if job.kind != JOB_KIND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such job.")
    payload = job.to_dict()
    payload["result"] = job.result
    return payload


@router.get("/api/references/{list_id}/review")
def stored_review(list_id: int,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    """The most recent review of this list, or 404 when there is none."""
    _get_list_or_404(ctx, user_id, list_id)
    found = user_store.latest_review(ctx.db, user_id, list_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="This list has not been reviewed yet.")
    return found


@router.get("/api/references/{list_id}/review-preview")
def review_preview(list_id: int, item_ids: Optional[str] = None,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """What a review of this selection would read, and what it would cost.

    The cost here is near-exact rather than a range: the prompt is built from
    text already stored, so its size is known before anything is sent (M5.A.2).
    Nothing is called and nothing is stored.
    """
    from src import tokens

    _get_list_or_404(ctx, user_id, list_id)
    _, gathered = _gather_for(ctx, list_id, _parse_item_ids(item_ids))
    built = review_builder.build_prompt(gathered, _max_prompt_chars(ctx.llm_config))

    # No credentials are resolved here at all. This used to call _resolve_for
    # — the RESERVING variant — only to read a model name, and threw the
    # reservation away: on the ordinary shared-key deployment every click on
    # Review checked consumed a slot of the day's allowance before the user
    # confirmed, and enough previews would refuse a real review (gate
    # 2026-09-21 finding 1, HIGH). It also reported the owner's model to a
    # user whose own key sat in the browser. The page asks POST
    # /api/usage/estimate who pays, which resolves without reserving; the
    # preview only says what would be read.
    prompt_tokens = tokens.estimate_text_tokens(built["prompt"], ctx.llm_config)
    return {
        "papers": len(built["included"]),
        "basis_note": review_builder.basis_note(built["included"]),
        "contributors": built["included"],
        "left_out": list(built["dropped"]) + list(built["excluded"]),
        "prompt_tokens": prompt_tokens,
    }
