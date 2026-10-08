"""
Purpose: AI-powered keyword discovery — run a broad search, ask LLM for term suggestions.
Spec:    docs/web_parity_spec_2026-09-17.md#FP1-B
Tests:   tests/web/test_discover_routes.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import user_store

from src.discover import (DiscoverParseError, DiscoverSettings, build_discover_prompt,
                          build_replace_prompt, check_terms, count_term_hits,
                          discover_settings, europepmc_term_count, parse_terms,
                          query_to_keywords)
from src.sources.config import polite_user_agent
from src.jobs import Job, JobLookup
from src.llm_providers import LLMError, NoLLMCredentialError, ProviderResponseError

from .auth import current_user, get_context
from .deps import AppContext
from .routes_searches import record_failure
from src import spend

from .routes_summaries import (_resolve_for, already_running, releases_request_connection,
                              submit_billed)

logger = logging.getLogger(__name__)
router = APIRouter()

JOB_KIND = "discover"

class DiscoverRequest(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="", max_length=100)
    sources: Optional[Dict[str, Any]] = None
    # Inline key (localStorage) — same pattern as summaries
    api_key: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=50)
    model: str = Field(default="", max_length=200)


def _europepmc_counter(ctx: AppContext, settings: DiscoverSettings):
    """term -> Europe PMC hit count (None when it could not answer). A seam
    for tests, which must never reach the network."""
    session = requests.Session()
    session.headers.update({"User-Agent": polite_user_agent(ctx.sources_config)})

    def count(term: str):
        return europepmc_term_count(term, settings.days_back, session.get,
                                    settings.check_timeout_s)
    return count


def _run_discover(ctx: AppContext, user_id: str, body: DiscoverRequest, resolved,
                  usage_id: Optional[int] = None):
    settings = discover_settings(ctx.llm_config)

    def work(job: Job) -> Dict[str, Any]:
        # Settlement (spend or give the slot back; release the connection) is
        # BilledCall's job, the same for every billed route (review M12).
        with spend.BilledCall(ctx.db, user_id, resolved, usage_id, job) as call:
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

            def on_source_failure(source_name: str, kind: str):
                record_failure(job, source_name, kind, ctx.sources_config)

            ctx.get_orchestrator().search(
                filter_dict=filter_dict,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=lambda *_: None,
                on_status=on_status,
                should_stop=job.should_stop,
                max_results=settings.max_papers,
                on_source_failure=on_source_failure,
                # Only titles and abstracts are read below; enriching every
                # paper cost up to two HTTP calls each for nothing (review M2).
                enrich_only=lambda _r: False,
            )

            prompt, sampled = build_discover_prompt(body.description, papers,
                                                    settings.max_papers)
            if not prompt:
                # Nothing to ask the model about. Say so, with the keywords
                # searched, rather than returning an empty list that reads as
                # "the model had no ideas". papers_found is the real number:
                # papers with no title are found but cannot be sampled.
                return {"terms": [], "papers_found": len(papers), "keywords": keywords,
                        "papers_sampled": 0, "term_hits": {}, "live_hits": {},
                        "dropped": [], "days_back": settings.days_back}

            job.phase = f"Asking {resolved.provider} ({len(papers)} papers found)"
            call.model_called()
            raw, usage = resolved.client.generate(prompt, context=settings.system_prompt)
            job.token_usage = usage
            try:
                terms = parse_terms(raw)
            except DiscoverParseError as e:
                logger.warning("Discover: unusable reply from %s: %s", resolved.provider, e)
                raise ProviderResponseError(
                    f"{resolved.provider} did not return a list of terms ({e})."
                ) from e

            # DT9: offer only terms that find papers. Each is checked with the
            # Europe PMC query a filter made from it sends; terms finding 0
            # are replaced once, and those still at 0 are dropped and named.
            # A term that could not be checked (None) is kept (P1).
            count = _europepmc_counter(ctx, settings)

            def progress(i: int, n: int):
                job.phase = f"Checking terms in Europe PMC ({i} of {n})"

            live = check_terms(terms, count, settings.check_delay_s, progress)
            failed = [t for t in terms if live[t] == 0]
            new_terms: List[str] = []
            if failed:
                job.phase = (f"Asking {resolved.provider} to replace {len(failed)} "
                             "term(s) that found no papers")
                try:
                    raw2, usage2 = resolved.client.generate(
                        build_replace_prompt(prompt, failed, settings.replace_prompt),
                        context=settings.system_prompt)
                    job.token_usage = job.token_usage + usage2
                    seen = {t.lower() for t in terms}
                    for t in parse_terms(raw2):
                        if t.lower() not in seen:
                            seen.add(t.lower())
                            new_terms.append(t)
                except (LLMError, DiscoverParseError) as e:
                    # The first terms still stand; say what the round cost.
                    # UNCOUNTED on an LLMError means the call never reached the
                    # model, so it adds nothing and must not turn the first
                    # call's known cost into "not reported" (DT9 gate F1).
                    carried = getattr(e, "usage", None)
                    if carried is not None and carried.counted:
                        job.token_usage = job.token_usage + carried
                    logger.warning("Discover: replacement round failed: %s", e)
                live.update(check_terms(new_terms, count, settings.check_delay_s, progress))

            reason = (f"0 papers in Europe PMC with it in the title or abstract, "
                      f"last {settings.days_back} days")
            candidates = list(dict.fromkeys(terms + new_terms))   # in order, once
            offered = [t for t in candidates if live[t] != 0]
            dropped = [{"term": t, "reason": reason} for t in candidates if live[t] == 0]
            if dropped:
                logger.info("Discover: dropped %d of %d terms that found no papers: %s",
                            len(dropped), len(candidates),
                            [d["term"] for d in dropped])

            # term_hits and days_back let the page say which terms occur in
            # the sample and give a filter made from a term the same window
            # (docs/implementation_plan_2026-10-07_discover_terms.md DT6, DT8).
            return {"terms": offered, "papers_found": len(papers), "keywords": keywords,
                    "papers_sampled": len(sampled),
                    "term_hits": count_term_hits(offered, sampled),
                    "live_hits": {t: live[t] for t in offered},
                    "dropped": dropped,
                    "days_back": settings.days_back}

    return work


@router.post("/api/discover-terms", status_code=status.HTTP_202_ACCEPTED)
@releases_request_connection
def discover_terms(body: DiscoverRequest,
                   ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    """Queue a term-discovery job. Poll GET /api/discover-terms/{job_id}."""
    # Refused before a slot is reserved (review M2): "   " passed min_length
    # and ran an unfiltered search of every source plus a model call.
    settings = discover_settings(ctx.llm_config)
    if not query_to_keywords(body.description, settings.stop_words).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Describe what you are looking for in a few words first.")
    running = already_running(ctx, JOB_KIND, user_id, ("discover",))
    if running is not None:
        return running
    resolved, usage_id = _resolve_for(ctx, user_id, inline_key=body.api_key,
                                      inline_provider=body.provider,
                                      inline_model=body.model)
    job = submit_billed(ctx, JOB_KIND, user_id,
                        _run_discover(ctx, user_id, body, resolved, usage_id),
                        usage_id, key=("discover",))
    if not hasattr(job, "to_dict"):
        return job                                   # the 409 response
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
