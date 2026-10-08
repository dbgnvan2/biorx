"""
Purpose: Summarize one paper as a background job, under the owner-key spend cap.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, #1.4, W2.d, D2
Tests:   tests/web/test_summaries_routes.py, tests/web/test_cap.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from src.config_values import config_int
from pydantic import BaseModel, Field

from src import spend, user_store
from src.jobs import Job, JobLookup
from src.llm_providers import (LLMError, NoLLMCredentialError,  # noqa: F401 (re-exported)
                               ProviderResponseError)
from src.paper_meta import recover_abstract, summary_pdf_link

from .auth import current_user, get_context
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()


# A ceiling on the request body itself, so an oversized paper is refused at the
# boundary rather than truncated silently deep inside the prompt builder
# (security S2: validate at the entry point).
MAX_PAPER_BYTES = 200_000


class SummaryRequest(BaseModel):
    paper: Dict[str, Any] = Field(default_factory=dict)
    # Inline credentials from localStorage: sent per-request, never stored on
    # the server. Allows BYO key without requiring KEY_ENC_SECRET.
    api_key: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=50)
    model: str = Field(default="", max_length=200)
    # Look for free copies by title as well as DOI (plan 2026-09-19 C2). None
    # = sources_config.yaml full_text.find_by_title (default on).
    find_by_title: Optional[bool] = None


# Admission and settlement live in src/spend.py (review M12). These wrappers
# turn its refusals into HTTP answers for every route that spends.
USAGE_KIND = spend.USAGE_KIND


def resolve_credentials(ctx: AppContext, user_id: str, inline_key: str = "",
                        inline_provider: str = "", inline_model: str = ""):
    """Which client would run for this user, reserving nothing (spend.resolve_credentials)."""
    try:
        return spend.resolve_credentials(ctx.db, ctx.llm_config, user_id, inline_key,
                                         inline_provider, inline_model)
    except spend.SpendRefused as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


def _resolve_for(ctx: AppContext, user_id: str,
                 inline_key: str = "", inline_provider: str = "",
                 inline_model: str = ""):
    """Resolve and admit a billed call (spend.admit): 400 no key, 429 cap, 503."""
    try:
        return spend.admit(ctx.db, ctx.llm_config, user_id, inline_key,
                           inline_provider, inline_model)
    except spend.SpendRefused as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


def releases_request_connection(route):
    """Purpose: Give the request thread's database connection back on every
             exit of a billed route — success, 409, and admission failures
             (batch-4 finding 2, batch-5 finding 1).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M12
    Tests:   tests/web/test_cap.py::test_gate5_connection_released_when_admission_fails
    """
    import functools

    @functools.wraps(route)
    def wrapper(*args, **kwargs):
        try:
            return route(*args, **kwargs)
        finally:
            kwargs["ctx"].db.release()
    return wrapper


def already_running(ctx: AppContext, kind: str, user_id: str, key):
    """A 409 answer if this user's job with this key is still going, else None.

    Checked before admission (batch-4 gate finding 1): admission reserves a
    slot, so at the cap limit a duplicate was answered 429 "add your own key"
    instead of "already running".
    """
    from fastapi.responses import JSONResponse
    job = ctx.jobs.find_running(user_id, key)
    if job is None:
        return None
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
        "detail": f"This {kind} is already running — its result will show when it is done.",
        "job_id": job.id})


# A run that needs no model call (a stored full-text summary is returned).
_NO_SPEND = spend.NO_SPEND


def submit_billed(ctx: AppContext, kind: str, user_id: str, work, usage_id,
                  key=None):
    """Queue a billed job, or answer 409 with the running one (review A7).

    The reserved slot is given back if the job never runs, or if it is refused
    here (review A14).
    """
    from fastapi.responses import JSONResponse
    from src.jobs import JobAlreadyRunning, TooManyJobs
    try:
        return ctx.jobs.submit(kind, user_id, work, key=key,
                               on_never_ran=lambda _j: spend.release_unused(ctx.db, usage_id))
    except TooManyJobs as e:
        spend.release_unused(ctx.db, usage_id)
        return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={
            "detail": f"You already have {e.limit} jobs waiting or running — "
                      "wait for some to finish, then try again.",
            # Not the daily allowance: the page reads a bare 429 as that.
            "reason": "too_many_jobs"})
    except JobAlreadyRunning as e:
        spend.release_unused(ctx.db, usage_id)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
            "detail": f"This {kind} is already running — its result will show when it is done.",
            "job_id": e.job.id})
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The server is restarting — try again in a minute.") from e
    finally:
        # The request thread used a connection for admission; give it back on
        # every path, not only the 409 one (batch-4 gate finding 2). The
        # routes also release on their own exits (releases_request_connection).
        ctx.db.release()


# The finders' HTTP (OpenAlex, Semantic Scholar, Unpaywall). None = the real
# one; tests replace it so no test can reach those services (tests/web/conftest).
_FINDER_GET_JSON = None


def _download_pdf_text(url: str) -> str:
    """The shared, SSRF-guarded download (src/fulltext.download_pdf_text)."""
    from src.fulltext import download_pdf_text
    return download_pdf_text(url)


def _extract_text(ctx: AppContext, paper: Dict[str, Any],
                  outcome: Optional[Dict[str, str]] = None,
                  by_title: Optional[bool] = None) -> str:
    """Purpose: The paper's full text, searched for in every free source, or "".
    Spec:    docs/implementation_plan_2026-09-19_full_text.md#C2
    Tests:   tests/web/test_summaries_routes.py::test_ft1_3_full_text_source_is_recorded

    `outcome["full_text"]` is "used" or where it looked and what each place
    said; `outcome["text_source"]` names where the text came from.
    """
    from src.fulltext import find_full_text
    from src.sources.config import get_unpaywall_email, polite_user_agent

    outcome = outcome if outcome is not None else {}
    cfg = ctx.sources_config or {}
    settings = cfg.get("full_text") or {}
    if by_title is None:
        by_title = bool(settings.get("find_by_title", True))
    own = summary_pdf_link(paper)
    found = find_full_text(
        paper, _download_pdf_text,
        own_links=[own] if own else [],
        by_title=by_title,
        email=get_unpaywall_email(cfg),
        max_downloads=config_int(settings, "max_downloads", 4,
                                 name="full_text.max_downloads"),
        user_agent=polite_user_agent(cfg),
        get_json=_FINDER_GET_JSON,
        refusal_notes=settings.get("refused_download_notes") or {},
    )
    if found.found:
        outcome["full_text"] = "used"
        outcome["text_source"] = found.source
        return found.text
    outcome["full_text"] = found.explain()
    outcome["text_source"] = ""
    logger.info("No full text for %s — %s",
                paper.get("doi") or paper.get("canonical_id"), found.explain())
    return ""


# The source lookup for a paper nobody has stored. None = the real one
# (src/summarize.lookup_at_source); tests replace it (tests/web/conftest).
_PAPER_LOOKUP = None


def _run_summary(ctx: AppContext, user_id: str, ref: Dict[str, str], resolved,
                 usage_id: Optional[int] = None, find_by_title: Optional[bool] = None):
    """Purpose: The summary job: resolve the paper server-side, then summarize it.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A1, #S1, #A8
    Tests:   tests/web/test_summaries_routes.py::test_a1_client_abstract_never_stored,
             tests/web/test_summaries_routes.py::test_a8_db_error_reports_not_saved
    """
    from .routes_searches import SEARCH_JOB_KINDS as SEARCH_RESULT_KINDS
    from src.summarize import (PaperLookupError, PaperNotFoundError, resolve_paper,
                               summarize_paper)

    def work(job: Job) -> Dict[str, Any]:
        with spend.BilledCall(ctx.db, user_id, resolved, usage_id, job) as call:
            job.phase = "Looking up the paper"
            try:
                paper = resolve_paper(
                    ctx.db, ref,
                    candidates=ctx.jobs.finished_results(user_id, SEARCH_RESULT_KINDS),
                    sources_config=ctx.sources_config, lookup=_PAPER_LOOKUP)
            except PaperLookupError as e:
                raise PaperLookupError(
                    f"Could not look up this paper's details ({e}) — try again later.") from e
            except PaperNotFoundError as e:
                raise PaperNotFoundError(
                    f"This paper could not be found at its source, so it was not "
                    f"summarized ({e}).") from e

            def find_text(p):
                outcome: Dict[str, str] = {}
                text = _extract_text(ctx, p, outcome, by_title=find_by_title)
                return text, outcome.get("full_text", ""), outcome.get("text_source", "")

            def model_call():
                call.model_called()
                job.phase = f"Summarizing with {resolved.provider}"

            def recover(p):
                # Guarded: the URLs it scrapes come from the paper record.
                from src.safe_fetch import fetch_html
                return recover_abstract(p, fetch_html=fetch_html)

            def get_client():
                if resolved.client is None:
                    # Admitted as a free reuse of a stored full-text summary,
                    # but the worker found none (batch-5 finding 2). Say so
                    # rather than failing on a missing client.
                    raise ProviderResponseError(
                        "The stored summary for this paper changed while this ran — "
                        "run Summarize again.")
                return resolved.client, resolved.model

            outcome = summarize_paper(
                ctx.db, paper, get_client,
                find_text=find_text, recover=recover, llm_config=ctx.llm_config,
                created_by=user_id,
                on_phase=lambda m: setattr(job, "phase", m),
                on_model_call=model_call,
                on_usage=lambda u: setattr(job, "token_usage", u))

            result: Dict[str, Any] = {
                "paper_id": outcome.paper_id,
                "source_text": outcome.source_text,
                "full_text": outcome.full_text,
                "text_source": outcome.text_source,
                "saved": outcome.saved,
                "reused": outcome.reused,
                **outcome.summary,
            }
            if not outcome.saved:
                # Shown but not kept (A8): the user must know a re-run bills again.
                result["not_saved"] = (
                    f"This summary was shown but could not be saved ({outcome.not_saved_reason}). "
                    "Running it again will call the model again.")
            if outcome.source_text == "abstract":
                result.update(abstract=outcome.abstract, provider="", model="",
                              key_source="none")
            elif outcome.reused:
                result.update(provider="", model=outcome.model, key_source="none")
            else:
                result.update(provider=resolved.provider, model=resolved.model,
                              key_source=resolved.key_source)
            return result

    return work


@router.post("/api/summaries", status_code=status.HTTP_202_ACCEPTED)
@releases_request_connection
def start_summary(body: SummaryRequest,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    """Queue a summary. Credential problems are answered now, not in the job."""
    if not body.paper:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No paper supplied.")
    import json as _json
    if len(_json.dumps(body.paper)) > MAX_PAPER_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That paper is larger than {MAX_PAPER_BYTES:,} bytes.",
        )
    from src.summarize import paper_ref
    ref = paper_ref(body.paper)
    if not ref["doi"] and not ref["canonical_id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The paper has no DOI or id, so it cannot be looked up.")
    key = ("paper", ref["doi"] or ref["canonical_id"])
    running = already_running(ctx, "summary", user_id, key)
    if running is not None:
        return running
    # A paper with a stored full-text summary gets it back without a model
    # call, so it needs no key and no allowance — not even at the cap
    # (batch-4 gate finding 1).
    row = ctx.db.find_paper(ref)
    stored = ctx.db.get_summary(row["id"]) if row else None
    if stored and stored.get("source_text") == "full_text":
        resolved, usage_id = _NO_SPEND, None
    else:
        resolved, usage_id = _resolve_for(ctx, user_id, inline_key=body.api_key,
                                          inline_provider=body.provider,
                                          inline_model=body.model)
    # Only the paper's identity is taken from the request (review A1); its
    # content comes from the server's own copy, resolved in the job. One run
    # per paper per user at a time (review A7).
    job = submit_billed(ctx, "summary", user_id,
                        _run_summary(ctx, user_id, ref, resolved, usage_id,
                                     find_by_title=body.find_by_title),
                        usage_id, key=key)
    if not hasattr(job, "to_dict"):
        return job                                   # the 409 response
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


@router.post("/api/summaries/lookup")
def lookup_summary(body: SummaryRequest,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """Return an existing summary for this paper, or 404.

    Summaries were being stored and never read back, so every Summarize re-ran
    the model and re-billed a key for a paper someone had already done. One
    summary per paper is shared by design (§2.5), so this is a straight saving.
    """
    # By DOI or canonical_id: a lookup keyed on DOI alone could never find a
    # summary of an arXiv paper, so every click re-ran the model.
    existing = ctx.db.find_paper(body.paper)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No stored summary.")
    summary = ctx.db.get_summary(existing["id"])
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No stored summary.")
    return {"paper_id": existing["id"], **summary}


@router.get("/api/papers/{paper_id}/summary")
def stored_summary(paper_id: int,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    summary = ctx.db.get_summary(paper_id)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No summary for that paper yet.")
    return summary
