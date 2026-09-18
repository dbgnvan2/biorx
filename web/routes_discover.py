"""
Purpose: AI-powered keyword discovery — run a broad search, ask LLM for term suggestions.
Spec:    docs/web_parity_spec_2026-09-17.md#FP1-B
Tests:   tests/web/test_discover_routes.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import user_store
from src.discover import (DiscoverParseError, discover_settings, parse_terms,
                          query_to_keywords)
from src.jobs import Job, JobLookup
from src.llm_providers import LLMError, NoLLMCredentialError, ProviderResponseError

from .auth import current_user, get_context
from .deps import AppContext
from .routes_searches import source_from_failure_status
from .routes_summaries import _resolve_for

logger = logging.getLogger(__name__)
router = APIRouter()

JOB_KIND = "discover"

_DISCOVER_SYSTEM_PROMPT = (
    "You are a research librarian. Given a list of paper titles and abstracts, "
    "suggest 5 to 10 concise keyword phrases (2–4 words each) that would be "
    "effective as database search terms to find similar papers. "
    "Respond with a JSON object: {\"terms\": [\"term1\", \"term2\", ...]}. "
    "No explanation, only the JSON object."
)


class DiscoverRequest(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    sources: Optional[Dict[str, Any]] = None
    # Inline key (localStorage) — same pattern as summaries
    api_key: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=50)
    model: str = Field(default="", max_length=200)


def _run_discover(ctx: AppContext, user_id: str, body: DiscoverRequest, resolved,
                  usage_id: Optional[int] = None):
    settings = discover_settings(ctx.llm_config)

    def work(job: Job) -> Dict[str, Any]:
        provider_called = False
        try:
            job.phase = "Searching for papers"
            keywords = query_to_keywords(body.description, settings.stop_words)
            filter_dict: Dict[str, Any] = {
                "text_groups": [{"title": "", "abstract": "", "both": keywords}],
                "days_back": settings.days_back,
            }
            if body.category:
                filter_dict["category"] = body.category

            source_selection = body.sources or {"all": True, "selected": []}
            papers: List[Dict[str, Any]] = []

            def on_batch(records):
                papers.extend(r.to_dict() for r in records)
                job.matched = len(papers)

            def on_status(message: str):
                job.phase = message
                failed = source_from_failure_status(message)
                if failed and failed not in job.sources_failed:
                    job.sources_failed.append(failed)

            ctx.get_orchestrator().search(
                filter_dict=filter_dict,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=lambda *_: None,
                on_status=on_status,
                should_stop=job.should_stop,
                max_results=settings.max_papers,
            )

            context_parts = []
            for p in papers[:settings.max_papers]:
                title = (p.get("title") or "").strip()
                abstract = (p.get("abstract") or "")[:300].strip()
                if title:
                    context_parts.append(f"Title: {title}")
                    if abstract:
                        context_parts.append(f"Abstract: {abstract}")

            if not context_parts:
                # Nothing to ask the model about. Say so, with the keywords
                # searched, rather than returning an empty list that reads as
                # "the model had no ideas".
                return {"terms": [], "papers_found": 0, "keywords": keywords}

            prompt = (
                f"Research interest: {body.description}\n\n"
                "Papers found:\n" + "\n".join(context_parts)
            )

            job.phase = f"Asking {resolved.provider} ({len(papers)} papers found)"
            provider_called = True
            raw = resolved.client.generate(prompt, context=_DISCOVER_SYSTEM_PROMPT)
            try:
                terms = parse_terms(raw)
            except DiscoverParseError as e:
                logger.warning("Discover: unusable reply from %s: %s", resolved.provider, e)
                raise ProviderResponseError(
                    f"{resolved.provider} did not return a list of terms ({e})."
                ) from e

            if usage_id is not None:
                user_store.finalize_usage(ctx.db, usage_id, resolved.provider,
                                          resolved.model)
            return {"terms": terms, "papers_found": len(papers), "keywords": keywords}
        except BaseException:
            # Same rule as summaries: give the owner-key slot back only when
            # the provider was never reached.
            if usage_id is not None and not provider_called:
                user_store.release_usage(ctx.db, usage_id)
            raise
        finally:
            ctx.db.release()

    return work


@router.post("/api/discover-terms", status_code=status.HTTP_202_ACCEPTED)
def discover_terms(body: DiscoverRequest,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """Queue a term-discovery job. Poll GET /api/discover-terms/{job_id}."""
    try:
        resolved, usage_id = _resolve_for(ctx, user_id,
                                          inline_key=body.api_key,
                                          inline_provider=body.provider,
                                          inline_model=body.model)
    except NoLLMCredentialError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc)) from exc

    job = ctx.jobs.submit(JOB_KIND, user_id,
                          _run_discover(ctx, user_id, body, resolved, usage_id))
    payload = job.to_dict()
    payload["provider"] = resolved.provider
    payload["model"] = resolved.model
    return payload


@router.get("/api/discover-terms/{job_id}")
def discover_status(job_id: str,
                    ctx: AppContext = Depends(get_context),
                    user_id: str = Depends(current_user)):
    """Poll a discover job. The result (terms) is included once it is done —
    the search poll payload deliberately omits results, so the client cannot
    read terms from /api/searches/{id}."""
    job, reason = ctx.jobs.lookup(job_id, user_id)
    if reason == JobLookup.EXPIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE,
                            detail="That job has expired — run it again.")
    if job is None or job.kind != JOB_KIND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such job.")
    payload = job.to_dict()
    payload["result"] = job.result if job.status == "done" else None
    return payload
