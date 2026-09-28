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
from src.llm_config import non_article_kind, summary_daily_cap
from src.llm_providers import LLMError, NoLLMCredentialError, resolve_client
from src.paper_meta import pdf_url, recover_abstract

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


# Every model call the web app makes is logged and capped under this one kind.
# Discover shares it deliberately: it draws on the same daily allowance as a
# summary, and _resolve_for is the shared admission gate for both. Logging the
# two under different kinds would put one action in the log under two names
# depending on who paid for it, and would take discover out of the cap's count.
# Splitting them into separate budgets is a deliberate change, not a side
# effect of token accounting — see TODO.
USAGE_KIND = "summary"


def resolve_credentials(ctx: AppContext, user_id: str, inline_key: str = "",
                        inline_provider: str = "", inline_model: str = ""):
    """Which client would run for this user, reserving nothing.

    Split out of _resolve_for so anything that needs to know who would pay —
    the pre-spend estimate, above all — asks the same question the spend path
    asks. A second copy of this logic answered it differently for a key held
    only in localStorage: the estimate named the wrong payer, showed an
    allowance that did not apply, and could refuse a run the user's own key
    would have paid for (gate 2026-09-21 finding 1). A surface that lies about
    who is being charged is the one thing a spend dialog must never do.
    """
    if inline_key.strip():
        # Inline path: key comes from localStorage, no server storage required.
        return resolve_client(user_provider=inline_provider,
                              user_key=inline_key.strip(),
                              user_model=inline_model, config=ctx.llm_config)

    try:
        provider, key = user_store.get_llm_key(ctx.db, user_id)
    except KeyEncryptionUnavailable as e:
        # A stored key that will not decrypt must not silently fall through to
        # the owner's credential (learnings P2).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Your stored key could not be read: {e}",
        ) from e

    user_data = user_store.get_user(ctx.db, user_id) or {}
    return resolve_client(user_provider=provider, user_key=key,
                          user_model=user_data.get("preferred_model") or "",
                          config=ctx.llm_config)


def _resolve_for(ctx: AppContext, user_id: str,
                 inline_key: str = "", inline_provider: str = "",
                 inline_model: str = ""):
    """Resolve this user's LLM client, honouring the owner-key spend cap.

    The cap exists because the access code is shared: without it, anyone holding
    the code can spend the owner's credential without limit. A user on their own
    key is not capped.

    inline_key, when provided, is used directly without touching the database.
    It is never stored — it travels from the browser's localStorage per-request.
    """
    resolved = resolve_credentials(ctx, user_id, inline_key, inline_provider,
                                   inline_model)
    if not resolved.billed_to_owner:
        return resolved, None

    usage_id = None
    if resolved.billed_to_owner:
        cap = summary_daily_cap(ctx.llm_config)
        # Reserve the slot here, atomically. Reading the count now and writing
        # the usage row after the job finishes is a check-then-act race: a burst
        # of requests all observe the same count and all pass.
        usage_id = user_store.reserve_owner_usage(
            ctx.db, user_id, USAGE_KIND, cap, resolved.provider, resolved.model
        )
        if usage_id is None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You have used today's {cap} summaries on the shared key. "
                    "Add your own API key in LLM settings to continue."
                ),
            )
    return resolved, usage_id


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
    own = pdf_url(paper)
    found = find_full_text(
        paper, _download_pdf_text,
        own_links=[own] if own else [],
        by_title=by_title,
        email=get_unpaywall_email(cfg),
        max_downloads=int(settings.get("max_downloads", 4)),
        user_agent=polite_user_agent(cfg),
        get_json=_FINDER_GET_JSON,
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
        provider_called = False
        recorded = False
        try:
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
                nonlocal provider_called
                provider_called = True
                job.phase = f"Summarizing with {resolved.provider}"

            def recover(p):
                # Guarded: the URLs it scrapes come from the paper record.
                from src.safe_fetch import fetch_html
                return recover_abstract(p, fetch_html=fetch_html)

            outcome = summarize_paper(
                ctx.db, paper, lambda: (resolved.client, resolved.model),
                find_text=find_text, recover=recover, llm_config=ctx.llm_config,
                created_by=user_id,
                on_phase=lambda m: setattr(job, "phase", m),
                on_model_call=model_call,
                on_usage=lambda u: setattr(job, "token_usage", u))

            if provider_called:
                user_store.record_spend(ctx.db, user_id, USAGE_KIND, resolved.provider,
                                        resolved.model, resolved.key_source,
                                        usage_id, job.token_usage)
                recorded = True
            elif usage_id is not None:
                user_store.release_usage(ctx.db, usage_id)   # nothing was spent

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
        except BaseException as exc:
            # The slot was reserved at admission. Give it back only when the
            # provider was never reached — a failure after the call may still
            # have cost money, and a cap is a spend ceiling, not an attempt
            # counter.
            if not provider_called:
                if usage_id is not None:
                    user_store.release_usage(ctx.db, usage_id)
            elif not recorded:
                # The model ran and was billed. The summary is lost; the record
                # of what it cost must not be (M1.B.3). A provider that raises
                # on an unusable reply carries the usage on the exception.
                spent = job.token_usage
                if not spent.counted:
                    spent = getattr(exc, "usage", None) or spent
                user_store.record_spend(ctx.db, user_id, USAGE_KIND,
                                        resolved.provider, resolved.model,
                                        resolved.key_source, usage_id, spent)
            raise
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
    import json as _json
    if len(_json.dumps(body.paper)) > MAX_PAPER_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That paper is larger than {MAX_PAPER_BYTES:,} bytes.",
        )
    try:
        resolved, usage_id = _resolve_for(ctx, user_id,
                                           inline_key=body.api_key,
                                           inline_provider=body.provider,
                                           inline_model=body.model)
    except NoLLMCredentialError as e:
        # The "no key at all" case (D2): a clean, actionable error, not a crash
        # and not a job that fails opaquely a minute later.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e)) from e

    from src.summarize import paper_ref
    ref = paper_ref(body.paper)
    if not ref["doi"] and not ref["canonical_id"]:
        if usage_id is not None:
            user_store.release_usage(ctx.db, usage_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The paper has no DOI or id, so it cannot be looked up.")
    # Only the paper's identity is taken from the request (review A1); its
    # content comes from the server's own copy, resolved in the job.
    job = ctx.jobs.submit("summary", user_id,
                          _run_summary(ctx, user_id, ref, resolved, usage_id,
                                       find_by_title=body.find_by_title))
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
