"""
Summarize one paper: the pipeline shared by the web app and the CLI.

Purpose: One implementation of "find the text, recover the abstract, call the
         model, validate, store", so the two entry points cannot drift apart.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S1, #A1, #M26, #A8, #M28
Tests:   tests/test_summarize.py, tests/web/test_summaries_routes.py

Spend accounting is not here: only the web app bills a shared key, so the
route wraps this function with its own admission and settlement (D6).

The paper this module stores always comes from a trusted place (review A1):
the stored row, a result the server itself produced for this user, or the
source looked up by DOI / id. Summaries are shared by every user, so a
paper's title, abstract or links taken from a request body would let anyone
change what everyone else sees.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.db import SummaryDowngradeRefused, SummaryNotSaved
from src.llm_config import non_article_kind
from src.llm_providers import ProviderResponseError, coerce_summary
from src.tokens import UNCOUNTED, TokenUsage

logger = logging.getLogger(__name__)


class PaperNotFoundError(Exception):
    """The paper is not stored, not in this user's results, and not at its source."""


class PaperLookupError(Exception):
    """A source could not be reached while looking the paper up. Retryable (P1)."""


# ── Which paper (review A1) ───────────────────────────────────────────────────

def paper_ref(paper: Dict[str, Any]) -> Dict[str, str]:
    """The only fields of a client-sent paper the server uses: its identity."""
    paper = paper or {}
    return {"doi": str(paper.get("doi") or "").strip(),
            "canonical_id": str(paper.get("canonical_id") or "").strip()}


def _same_paper(candidate: Dict[str, Any], ref: Dict[str, str]) -> bool:
    doi = ref.get("doi", "").lower()
    cid = ref.get("canonical_id", "")
    if doi and str(candidate.get("doi") or "").strip().lower() == doi:
        return True
    return bool(cid) and str(candidate.get("canonical_id") or "").strip() == cid


def _row_to_paper(row: Dict[str, Any]) -> Dict[str, Any]:
    paper = dict(row)
    paper.setdefault("source_url", paper.get("url") or "")
    return paper


def lookup_at_source(ref: Dict[str, str], sources_config: Optional[Dict[str, Any]] = None
                     ) -> Optional[Dict[str, Any]]:
    """Look a paper up at its source by id: arXiv, else Europe PMC, else Crossref.

    Purpose: The server's own copy of a paper nobody has stored yet.
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A1
    Tests:   tests/test_summarize.py::test_a1_arxiv_paper_resolved_at_source,
             tests/test_summarize.py::test_a1_doi_resolved_via_crossref,
             tests/test_summarize.py::test_a1_source_outage_is_retryable

    Returns None when no source has it. Raises PaperLookupError when a source
    could not answer, so the user is told to try again rather than "not found".
    """
    from src.sources.errors import RateLimitedError, SourceUnavailableError
    from src.sources.config import get_crossref_user_agent

    cfg = sources_config or {}
    doi = ref.get("doi", "")
    cid = ref.get("canonical_id", "")
    try:
        if cid.lower().startswith("arxiv:"):
            from src.sources.arxiv import ArxivAdapter
            adapter = ArxivAdapter(sources_config=cfg)
            raw = adapter.get_by_id(cid[6:])
            if raw:
                return adapter.normalize(raw).to_dict()
        pmid = cid[5:] if cid.lower().startswith("pmid:") else ""
        if doi or pmid:
            from src.sources.europepmc import EuropePmcAdapter
            epmc = EuropePmcAdapter(sources_config=cfg)
            raw = epmc.get_by_id(doi or pmid)
            if raw:
                record = epmc.normalize(raw)
                if record.title:
                    return record.to_dict()
        if doi:
            from src.sources.crossref import CrossrefAdapter
            record = CrossrefAdapter(user_agent=get_crossref_user_agent(cfg)).record_for_doi(doi)
            if record is not None:
                return record.to_dict()
    except (SourceUnavailableError, RateLimitedError) as exc:
        raise PaperLookupError(str(exc)) from exc
    return None


def resolve_paper(db, ref: Dict[str, str], *,
                  candidates: Iterable[List[Dict[str, Any]]] = (),
                  sources_config: Optional[Dict[str, Any]] = None,
                  lookup: Optional[Callable] = None) -> Dict[str, Any]:
    """The server's copy of the paper `ref` names.

    Purpose: Never take a paper's content from a request body (review A1).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#A1
    Tests:   tests/web/test_summaries_routes.py::test_a1_client_abstract_never_stored,
             tests/web/test_summaries_routes.py::test_a1_paper_from_own_search_is_used

    Order: the stored row; a paper in `candidates` (lists of papers the server
    produced for this user, e.g. finished search results); the source.
    """
    if not ref.get("doi") and not ref.get("canonical_id"):
        raise PaperNotFoundError("the paper has no DOI or id to look it up by")
    row = db.find_paper(ref)
    if row:
        return _row_to_paper(row)
    for results in candidates:
        for paper in results or []:
            if isinstance(paper, dict) and _same_paper(paper, ref):
                return dict(paper)
    found = (lookup or lookup_at_source)(ref, sources_config)
    if not found:
        raise PaperNotFoundError(
            f"{ref.get('doi') or ref.get('canonical_id')} was not found at its source")
    return found


# ── The pipeline ──────────────────────────────────────────────────────────────

@dataclass
class SummaryOutcome:
    """What one summarize run produced, and whether it was kept."""
    paper_id: Optional[int]
    source_text: str                       # "full_text" or "abstract"
    abstract: str = ""
    summary: Dict[str, Any] = field(default_factory=lambda: {
        "key_findings": [], "methodology": "", "conclusions": ""})
    full_text: str = ""                    # "used", or where it looked
    text_source: str = ""
    model: str = ""
    reused: bool = False                   # an existing full-text summary, no model call
    saved: bool = True
    not_saved_reason: str = ""
    usage: TokenUsage = UNCOUNTED


# find_text(paper) -> (text, note, text_source): note is "used" or what was tried.
FindText = Callable[[Dict[str, Any]], Tuple[str, str, str]]


def _store(db, paper: Dict[str, Any], **fields) -> Tuple[Optional[int], bool, str]:
    """Store the paper (if new) and its summary. (paper_id, saved, reason)."""
    try:
        paper_id = db.insert_paper(paper)
    except sqlite3.IntegrityError as exc:
        return None, False, f"the paper could not be stored ({exc})"
    if not paper_id:
        existing = db.find_paper(paper)
        paper_id = existing["id"] if existing else None
    if not paper_id:
        return None, False, "the paper could not be stored"
    try:
        db.insert_summary(paper_id, **fields)
    except SummaryDowngradeRefused as exc:
        return paper_id, False, str(exc)
    except SummaryNotSaved as exc:
        return paper_id, False, str(exc)
    return paper_id, True, ""


def summarize_paper(db, paper: Dict[str, Any], get_client: Callable[[], Tuple[Any, str]], *,
                    find_text: FindText,
                    recover: Optional[Callable[[Dict[str, Any]], Any]] = None,
                    llm_config: Optional[Dict[str, Any]] = None,
                    created_by: Optional[str] = None,
                    on_phase: Optional[Callable[[str], None]] = None,
                    on_model_call: Optional[Callable[[], None]] = None,
                    on_usage: Optional[Callable[[TokenUsage], None]] = None,
                    ) -> SummaryOutcome:
    """Summarize one (trusted) paper and store the result.

    get_client() returns (client, model name). It is called only when the
    model is actually needed, so a run that keeps the abstract needs no key.

    Purpose: The single summarize pipeline (review S1).
    Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#S1, #M26, #A8, #M28
    Tests:   tests/test_summarize.py::test_s1_route_and_cli_store_identical_rows,
             tests/test_summarize.py::test_m26_existing_full_text_is_reused,
             tests/test_summarize.py::test_a8_not_saved_is_reported

    - A paper that already has a full-text summary gets it back; the model is
      not called (M26).
    - With no full text, the abstract is stored as the entry and the model is
      not called (plan 2026-09-19 C1).
    - A summary the model produced but that could not be stored comes back
      with saved=False and the reason (A8); it is never reported as kept.
    Raises ProviderResponseError when there is nothing to summarize, and
    whatever the provider raises.
    """
    phase = on_phase or (lambda _m: None)

    stored = db.find_paper(paper)
    if stored:
        existing = db.get_summary(stored["id"])
        if existing and existing.get("source_text") == "full_text":
            return SummaryOutcome(
                paper_id=stored["id"], source_text="full_text",
                abstract=stored.get("abstract") or "",
                summary={"key_findings": existing.get("key_findings") or [],
                         "methodology": existing.get("methodology") or "",
                         "conclusions": existing.get("conclusions") or ""},
                full_text="used", text_source=existing.get("text_source") or "",
                model=existing.get("model_version") or "", reused=True)

    phase("Looking for the full text")
    full_text, note, text_source = find_text(paper)
    abstract = paper.get("abstract") or ""        # NULL in the database (M28)

    if not abstract and not full_text:
        # Many records arrive without an abstract even though one is a lookup
        # away — an open-access paper on PMC, for instance.
        phase("Looking for the abstract")
        recovered = recover(paper) if recover else None
        if recovered is not None and recovered.found:
            abstract = recovered.text
            paper["abstract"] = abstract       # stored with the paper below
            phase(f"Abstract found via {recovered.source}")
        else:
            notice = non_article_kind(llm_config or {}, paper.get("title", ""))
            if notice:
                raise ProviderResponseError(
                    f"This looks like a {notice.rstrip(':')} notice rather "
                    "than an article, and it has no abstract to summarize.")
            failed = list(dict.fromkeys(getattr(recovered, "failed", []) or []))
            tried_all = getattr(recovered, "tried", []) or []
            asked = [n for n in dict.fromkeys(tried_all) if n not in failed]
            tried = ", ".join(asked) or "nothing to look up"
            unreachable = (f" Could not reach: {', '.join(failed)} — try again later."
                           if failed else "")
            raise ProviderResponseError(
                "No abstract or downloadable text for this paper "
                f"(looked in: {tried}).{unreachable}")

    if not full_text:
        # No full text anywhere: the abstract stands in, and the model is not
        # called — a "summary" of an abstract reads as more than it is.
        phase("No full text found — keeping the abstract")
        paper_id, saved, reason = _store(
            db, paper, summary_text=abstract, model_version="",
            created_by_user_id=created_by, source_text="abstract")
        return SummaryOutcome(paper_id=paper_id, source_text="abstract",
                              abstract=abstract, full_text=note or "not found",
                              saved=saved, not_saved_reason=reason)

    if on_model_call:
        on_model_call()
    client, model = get_client()
    summary, usage = client.summarize_paper(abstract, full_text)
    # Reported before validation: an unusable reply still cost tokens.
    if on_usage:
        on_usage(usage)
    if summary is None:
        raise ProviderResponseError(f"{model or 'The model'} returned no summary.",
                                    usage=usage)
    summary = coerce_summary(summary)

    phase("Saving")
    # summary_text stays "" when the structured fields hold the summary: one
    # convention for every writer, so nothing reads the content twice (A13).
    paper_id, saved, reason = _store(
        db, paper, summary_text="", key_findings=summary.get("key_findings"),
        methodology=summary.get("methodology"), conclusions=summary.get("conclusions"),
        model_version=model, created_by_user_id=created_by,
        source_text="full_text", text_source=text_source)
    if not saved:
        logger.error("Summary for %s was produced but not stored: %s",
                     paper.get("doi") or paper.get("canonical_id"), reason)
    return SummaryOutcome(paper_id=paper_id, source_text="full_text", abstract=abstract,
                          summary=summary, full_text="used", text_source=text_source,
                          model=model, saved=saved, not_saved_reason=reason, usage=usage)
