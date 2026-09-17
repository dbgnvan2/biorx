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

from src.jobs import Job
from src.llm_providers import LLMError, NoLLMCredentialError, resolve_client

from .auth import current_user, get_context
from .deps import AppContext
from .routes_summaries import _resolve_for

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_PAPERS_FOR_CONTEXT = 30
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


def _run_discover(ctx: AppContext, user_id: str, body: DiscoverRequest, resolved):
    def work(job: Job) -> Dict[str, Any]:
        try:
            job.phase = "Searching for papers"
            filter_dict: Dict[str, Any] = {
                "keywords": body.description,
                "days_back": 90,
            }
            if body.category:
                filter_dict["category"] = body.category

            source_selection = body.sources or {"all": True, "selected": []}
            papers: List[Dict[str, Any]] = []

            def on_batch(records):
                papers.extend(r.to_dict() for r in records)

            ctx.get_orchestrator().search(
                filter_dict=filter_dict,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=lambda *_: None,
                on_status=lambda s: setattr(job, "phase", s),
                should_stop=job.should_stop,
                max_results=_MAX_PAPERS_FOR_CONTEXT,
            )

            job.phase = f"Asking LLM (found {len(papers)} papers)"

            # Build context from titles and abstracts
            context_parts = []
            for p in papers[:_MAX_PAPERS_FOR_CONTEXT]:
                title = (p.get("title") or "").strip()
                abstract = (p.get("abstract") or "")[:300].strip()
                if title:
                    context_parts.append(f"Title: {title}")
                    if abstract:
                        context_parts.append(f"Abstract: {abstract}")

            if not context_parts:
                return {"terms": []}

            prompt = (
                f"Research interest: {body.description}\n\n"
                "Papers found:\n" + "\n".join(context_parts[:200])
            )

            raw = resolved.client.generate(prompt, context=_DISCOVER_SYSTEM_PROMPT)

            import json as _json
            try:
                result = _json.loads(raw)
                terms = result.get("terms", [])
                if not isinstance(terms, list):
                    terms = []
                # Sanitize: strings only, max 100 chars each
                terms = [str(t)[:100] for t in terms if t][:20]
            except (_json.JSONDecodeError, AttributeError):
                terms = []

            return {"terms": terms}
        finally:
            ctx.db.release()

    return work


@router.post("/api/discover-terms", status_code=status.HTTP_202_ACCEPTED)
def discover_terms(body: DiscoverRequest,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """Queue a term-discovery job. Returns a job_id to poll (same as searches)."""
    try:
        resolved, _ = _resolve_for(ctx, user_id,
                                   inline_key=body.api_key,
                                   inline_provider=body.provider,
                                   inline_model=body.model)
    except NoLLMCredentialError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc)) from exc

    job = ctx.jobs.submit("discover", user_id,
                          _run_discover(ctx, user_id, body, resolved))
    payload = job.to_dict()
    payload["provider"] = resolved.provider
    payload["model"] = resolved.model
    return payload
