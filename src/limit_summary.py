"""
The "results are incomplete" summary: how far over each source was, and
what to do about it.

Purpose: Replace one run-on warning with a row per source (read N of M, and
         an action chosen for that source's situation) and a next step.
Spec:    docs/implementation_plan_2026-10-08_limits_and_warnings.md#WS3
Tests:   tests/test_limit_summary.py

Pure: built from what the search recorded on the job, the sources selected,
the filter and the config. The wording is in sources_config.yaml
(limit_summary, failure_explanations); this module only chooses.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

WORD_SOURCES = ("europepmc", "pubmed", "arxiv")
OSF_SOURCES = ("psyarxiv", "socarxiv")
LIMIT_KINDS = ("truncated", "page-limit")

_FALLBACK = {
    "next_step_order": ["osf_title_word", "untick_pubmed", "untick_biorxiv", "narrow_words",
                        "raise_limit", "raise_limit_unknown", "page_limit", "try_later"],
    "actions": {
        "untick_pubmed": "Part of Europe PMC — untick it.",
        "osf_title_word": "No Title word, so every paper in the date range is read. "
                          "Put a key word in the Title box.",
        "untick_biorxiv": "Its preprints come through Europe PMC — untick it.",
        "narrow_words": "Too many to read ({total:,}): narrow the words.",
        "raise_limit": "Raise the limit to {total:,} or less, or narrow the words.",
        "raise_limit_unknown": "Raise the limit, or narrow the words.",
        "page_limit": "Use a shorter date range.",
        "try_later": "Try again later.",
    },
    "next_step": {},
}


def _settings(sources_config: Optional[Mapping]) -> Mapping:
    block = (sources_config or {}).get("limit_summary")
    if not block or not block.get("actions"):
        logger.warning("sources_config has no limit_summary — using built-in wording")
        return _FALLBACK
    return block


def _osf_narrowed(filter_dict: Mapping, sources_config: Optional[Mapping]) -> bool:
    """True when PsyArXiv/SocArXiv can ask for title words (every group has one)."""
    from src.sources.query_builder import osf_title_terms
    max_terms = int(((sources_config or {}).get("osf") or {}).get("max_title_terms", 8))
    return bool(osf_title_terms(dict(filter_dict or {}), max_terms))


def _action_key(source: str, kind: str, total: Optional[int], active: Iterable[str],
                osf_narrowed: bool, ceiling: int) -> Optional[str]:
    active = set(active or [])
    if kind not in LIMIT_KINDS:
        return "untick_biorxiv" if source == "biorxiv_medrxiv" else "try_later"
    if kind == "page-limit":
        return "page_limit"
    if source == "pubmed" and "europepmc" in active:
        return "untick_pubmed"
    if source in OSF_SOURCES and not osf_narrowed:
        return "osf_title_word"
    if not total:
        return "raise_limit_unknown"
    return "raise_limit" if total <= ceiling else "narrow_words"


def _counts(source: str, kind: str, limit: Optional[Mapping], osf_narrowed: bool,
            reason: str) -> str:
    if kind not in LIMIT_KINDS or not limit:
        return reason
    read, total = int(limit.get("read") or 0), limit.get("total")
    if source in WORD_SOURCES:
        what = "matches"
    elif source in OSF_SOURCES and osf_narrowed:
        what = "papers with your Title words"
    else:
        what = "papers in the date range"
    if total:
        return f"read {read:,} of {int(total):,} {what}"
    return f"read {read:,} {what} (total not reported)"


def limit_summary(failures: Mapping[str, str], limits: Mapping[str, Mapping],
                  active_sources: Iterable[str], filter_dict: Mapping,
                  max_results: int, ceiling: int,
                  sources_config: Optional[Mapping]) -> Optional[Dict[str, Any]]:
    """The summary for a search, or None when every source was read in full.

    failures: source name -> failure kind (truncated, page-limit, unavailable, …)
    limits:   source name -> {"read", "total"} for sources a limit stopped
    """
    if not failures:
        return None
    from src.sources.config import source_label
    cfg = _settings(sources_config)
    actions, steps = cfg.get("actions") or {}, cfg.get("next_step") or {}
    reasons = (sources_config or {}).get("failure_explanations") or {}
    narrowed = _osf_narrowed(filter_dict, sources_config)
    rows: List[Dict[str, str]] = []
    keys: List[str] = []
    for source, kind in failures.items():
        limit = limits.get(source)
        total = int(limit["total"]) if limit and limit.get("total") else None
        key = _action_key(source, kind, total, active_sources, narrowed, ceiling)
        keys.append(key)
        template = str(actions.get(key) or _FALLBACK["actions"].get(key, ""))
        try:
            action = template.format(total=total or 0, limit=max_results)
        except (KeyError, ValueError, IndexError):
            action = template
        rows.append({"source": source, "label": source_label(source),
                     "counts": _counts(source, kind, limit, narrowed,
                                       str(reasons.get(kind) or "did not finish")),
                     "action": action})
    order = list(cfg.get("next_step_order") or _FALLBACK["next_step_order"])
    present = [k for k in order if k in keys][:2]
    phrases = [str(steps.get(k) or "").strip() for k in present]
    phrases = [p for p in phrases if p]
    n = len(rows)
    return {
        "heading": f"Results are incomplete — {n} source{'s' if n != 1 else ''} "
                   f"could not be read in full.",
        "next_step": ("Most useful next step: " + ", and ".join(phrases) + ".") if phrases else "",
        "rows": rows,
        "limit": max_results,
        "ceiling": ceiling,
    }
