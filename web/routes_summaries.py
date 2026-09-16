"""
Purpose: Summarize one paper as a background job, under the owner-key spend cap.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, #1.4, W2.d, D2
Tests:   tests/web/test_summaries_routes.py, tests/web/test_cap.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import user_store
from src.crypto import KeyEncryptionUnavailable
from src.jobs import Job, JobLookup
from src.llm_config import summary_daily_cap
from src.llm_providers import (
    LLMError, NoLLMCredentialError, ProviderResponseError, resolve_client,
)
from src.paper_meta import pdf_url

from .auth import current_user, get_context
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()


class SummaryRequest(BaseModel):
    paper: Dict[str, Any] = Field(default_factory=dict)


def _resolve_for(ctx: AppContext, user_id: str):
    """Resolve this user's LLM client, honouring the owner-key spend cap.

    The cap exists because the access code is shared: without it, anyone holding
    the code can spend the owner's credential without limit. A user on their own
    key is not capped.
    """
    try:
        provider, key = user_store.get_llm_key(ctx.db, user_id)
    except KeyEncryptionUnavailable as e:
        # A stored key that will not decrypt must not silently fall through to
        # the owner's credential (learnings P2).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Your stored key could not be read: {e}",
        ) from e

    resolved = resolve_client(user_provider=provider, user_key=key,
                              config=ctx.llm_config)

    if resolved.billed_to_owner:
        cap = summary_daily_cap(ctx.llm_config)
        used = user_store.owner_usage_today(ctx.db, user_id)
        if used >= cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You have used today's {cap} summaries on the shared key. "
                    "Add your own API key in LLM settings to continue."
                ),
            )
    return resolved


def _extract_text(ctx: AppContext, paper: Dict[str, Any]) -> str:
    """Get text to summarize: the PDF if we can fetch it, else the abstract."""
    from src.pdf_handler import PDFHandler

    url = pdf_url(paper)
    if not url:
        return ""
    try:
        handler = PDFHandler()
        path = handler.download_pdf(url, paper.get("title", ""), paper.get("doi", ""))
        if path:
            return handler.extract_text(path) or ""
    except Exception as e:
        # A paper whose PDF will not download is still summarizable from its
        # abstract; say so in the log rather than failing the job.
        logger.info("Could not extract PDF text for %s: %s",
                    paper.get("doi") or paper.get("canonical_id"), e)
    return ""


def _run_summary(ctx: AppContext, user_id: str, paper: Dict[str, Any], resolved):
    def work(job: Job) -> Dict[str, Any]:
        try:
            job.phase = "Fetching the paper"
            full_text = _extract_text(ctx, paper)
            abstract = paper.get("abstract", "") or ""
            if not abstract and not full_text:
                raise ProviderResponseError(
                    "No abstract and no downloadable text for this paper."
                )

            job.phase = f"Summarizing with {resolved.provider}"
            summary = resolved.client.summarize_paper(abstract, full_text)

            # OllamaClient returns None on failure while the hosted clients
            # raise; normalise here so the route has one contract (P22).
            if summary is None:
                raise ProviderResponseError(
                    f"{resolved.provider} returned no summary."
                )

            job.phase = "Saving"
            paper_id = ctx.db.insert_paper(paper)
            if paper_id:
                ctx.db.insert_summary(
                    paper_id,
                    summary_text="",
                    key_findings=summary.get("key_findings"),
                    methodology=summary.get("methodology"),
                    conclusions=summary.get("conclusions"),
                    model_version=resolved.model,
                    created_by_user_id=user_id,
                )
            user_store.record_usage(ctx.db, user_id, "summary", resolved.provider,
                                    resolved.model, resolved.key_source)
            return {
                "paper_id": paper_id,
                "provider": resolved.provider,
                "model": resolved.model,
                "key_source": resolved.key_source,
                **summary,
            }
        finally:
            ctx.db.release()

    return work


@router.post("/api/summaries", status_code=status.HTTP_202_ACCEPTED)
def start_summary(body: SummaryRequest,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    """Queue a summary. Credential problems are answered now, not in the job."""
    if not body.paper:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No paper supplied.")
    try:
        resolved = _resolve_for(ctx, user_id)
    except NoLLMCredentialError as e:
        # The "no key at all" case (D2): a clean, actionable error, not a crash
        # and not a job that fails opaquely a minute later.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e)) from e

    job = ctx.jobs.submit("summary", user_id,
                          _run_summary(ctx, user_id, body.paper, resolved))
    payload = job.to_dict()
    payload["provider"] = resolved.provider
    payload["model"] = resolved.model
    payload["key_source"] = resolved.key_source
    return payload


@router.get("/api/summaries/{job_id}")
def summary_status(job_id: str,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    job, reason = ctx.jobs.lookup(job_id, user_id)
    if reason == JobLookup.EXPIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE,
                            detail="That summary has expired — run it again.")
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such job.")
    payload = job.to_dict()
    payload["result"] = job.result
    return payload


@router.get("/api/papers/{paper_id}/summary")
def stored_summary(paper_id: int,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    summary = ctx.db.get_summary(paper_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No summary for that paper yet.")
    return summary
