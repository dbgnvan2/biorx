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
from src.llm_providers import (
    LLMError, NoLLMCredentialError, ProviderResponseError, _coerce_summary,
    resolve_client,
)
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
    if inline_key.strip():
        # Inline path: key comes from localStorage, no server storage required.
        resolved = resolve_client(user_provider=inline_provider, user_key=inline_key.strip(),
                                  user_model=inline_model, config=ctx.llm_config)
        return resolved, None

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
    preferred_model = user_data.get("preferred_model") or ""

    resolved = resolve_client(user_provider=provider, user_key=key,
                              user_model=preferred_model,
                              config=ctx.llm_config)

    usage_id = None
    if resolved.billed_to_owner:
        cap = summary_daily_cap(ctx.llm_config)
        # Reserve the slot here, atomically. Reading the count now and writing
        # the usage row after the job finishes is a check-then-act race: a burst
        # of requests all observe the same count and all pass.
        usage_id = user_store.reserve_owner_usage(
            ctx.db, user_id, "summary", cap, resolved.provider, resolved.model
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


def _extract_text(ctx: AppContext, paper: Dict[str, Any]) -> str:
    """Get text to summarize: the PDF if we can fetch it, else "".

    The paper dict comes from the client, so its URL does too. The PDF is
    fetched through src/safe_fetch (public https hosts only, every redirect
    checked) into a temporary file, and never into the shared PDF cache: that
    cache is keyed by DOI and title, which a client could use to plant a file
    that every later summary of the real paper would read.
    """
    import tempfile

    from src import safe_fetch
    from src.pdf_handler import PDFHandler

    url = pdf_url(paper)
    if not url:
        return ""
    try:
        data = safe_fetch.fetch_pdf(url)
    except (safe_fetch.FetchRefused, safe_fetch.FetchFailed,
            safe_fetch.NotAPdf, safe_fetch.TooLarge) as e:
        # A paper whose PDF will not download is still summarizable from its
        # abstract; say so in the log rather than failing the job.
        logger.info("No PDF text for %s (%s): %s",
                    paper.get("doi") or paper.get("canonical_id"), type(e).__name__, e)
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/paper.pdf"
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            return PDFHandler(tmp).extract_text(path) or ""
        except Exception as e:
            logger.info("Could not extract PDF text for %s: %s",
                        paper.get("doi") or paper.get("canonical_id"), e)
            return ""


def _paper_row_id(ctx: AppContext, paper: Dict[str, Any]) -> Optional[int]:
    """The paper's row id, inserting it if it is new.

    insert_paper() returns None for a paper already stored — the normal case for
    anyone summarizing a paper a colleague already saved. Looks the row up by
    DOI or canonical_id, because many papers (every arXiv record) have no DOI.
    """
    paper_id = ctx.db.insert_paper(paper)
    if paper_id:
        return paper_id
    existing = ctx.db.find_paper(paper)
    if existing:
        return existing["id"]
    logger.warning(
        "Could not store or find the paper row for %s — the summary will not "
        "be saved", paper.get("canonical_id") or (paper.get("title") or "")[:60],
    )
    return None


def _run_summary(ctx: AppContext, user_id: str, paper: Dict[str, Any], resolved,
                 usage_id: Optional[int] = None):
    def work(job: Job) -> Dict[str, Any]:
        provider_called = False
        try:
            job.phase = "Fetching the paper"
            full_text = _extract_text(ctx, paper)
            abstract = paper.get("abstract", "") or ""

            if not abstract and not full_text:
                # Many records arrive without an abstract even though one is a
                # lookup away — an open-access paper on PMC, for instance. Try
                # the same recovery chain the desktop app uses before giving up.
                job.phase = "Looking for the abstract"
                # Guarded: the URLs it scrapes come from the client's paper.
                from src.safe_fetch import fetch_html
                recovered = recover_abstract(paper, fetch_html=fetch_html)
                if recovered.found:
                    abstract = recovered.text
                    paper["abstract"] = abstract       # stored with the paper below
                    job.phase = f"Abstract found via {recovered.source}"
                else:
                    notice = non_article_kind(ctx.llm_config, paper.get("title", ""))
                    if notice:
                        raise ProviderResponseError(
                            f"This looks like a {notice.rstrip(':')} notice rather "
                            "than an article, and it has no abstract to summarize."
                        )
                    tried = ", ".join(dict.fromkeys(recovered.tried)) or "nothing to look up"
                    raise ProviderResponseError(
                        "No abstract or downloadable text for this paper "
                        f"(looked in: {tried})."
                    )

            job.phase = f"Summarizing with {resolved.provider}"
            provider_called = True
            summary = resolved.client.summarize_paper(abstract, full_text)

            # OllamaClient returns None on failure while the hosted clients
            # raise; normalise here so the route has one contract (P22).
            if summary is None:
                raise ProviderResponseError(
                    f"{resolved.provider} returned no summary."
                )
            # And it does no validation of what it did return. Its text parser
            # yields a dict of empty fields when the model answers in a shape it
            # does not recognise, which would be stored as a successful summary
            # and shown as a blank card. Put every provider through the same
            # check the hosted ones use.
            summary = _coerce_summary(summary)

            job.phase = "Saving"
            paper_id = _paper_row_id(ctx, paper)
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
            if usage_id is not None:
                user_store.finalize_usage(ctx.db, usage_id, resolved.provider,
                                          resolved.model)
            else:
                user_store.record_usage(ctx.db, user_id, "summary",
                                        resolved.provider, resolved.model,
                                        resolved.key_source)
            return {
                "paper_id": paper_id,
                "provider": resolved.provider,
                "model": resolved.model,
                "key_source": resolved.key_source,
                **summary,
            }
        except BaseException:
            # The slot was reserved at admission. Give it back only when the
            # provider was never reached — a failure after the call may still
            # have cost money, and a cap is a spend ceiling, not an attempt
            # counter.
            if usage_id is not None and not provider_called:
                user_store.release_usage(ctx.db, usage_id)
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

    job = ctx.jobs.submit("summary", user_id,
                          _run_summary(ctx, user_id, body.paper, resolved, usage_id))
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
