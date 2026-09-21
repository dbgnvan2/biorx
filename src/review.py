"""
One synthesis across several papers: themes, disagreements, gaps.

Purpose: Build the prompt for a cross-paper review, and say what went into it.
Spec:    docs/implementation_plan_2026-09-20_references_batch.md#M4
Tests:   tests/test_review.py

Only stored text is used — a summary if the paper has one, its abstract
otherwise. Nothing is fetched, which is what makes the cost knowable before the
run and keeps this module pure and testable offline (standards L9).

The rule this module exists to enforce: a synthesis must say what it read. A
review built from three abstracts and two full-text summaries is a different
object from one built from five full-text summaries, and presenting them
identically would overstate the second-hand ones. Every paper that contributed
is recorded with the basis it contributed on, and every paper that could not
contribute is recorded too (learnings P2).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# What a contributing paper was read from.
FULL_TEXT = "full_text"     # a model summary made from the paper's full text
ABSTRACT = "abstract"       # the abstract: either a stand-in summary, or raw
NOTHING = "nothing"         # neither: the paper cannot contribute

SYSTEM_PROMPT = (
    "You are helping a researcher review a set of papers together. "
    "Write one synthesis across all of them: the themes they share, where they "
    "disagree, and what is missing. Ground every claim in the papers given; "
    "where they do not support a conclusion, say so rather than supplying one. "
    "Refer to papers by their number. Do not invent findings, citations or "
    "numbers that are not in the material below."
)

DEFAULT_MAX_PROMPT_CHARS = 40000


def _text_for(paper: Mapping, summary: Optional[Mapping]) -> Tuple[str, str]:
    """(text, basis) for one paper: its summary, else its abstract, else none.

    A summary whose source_text is "abstract" is a kept abstract, not a model
    summary — it counts as ABSTRACT, because that is what the reviewer would
    be reading.
    """
    if summary:
        parts = []
        findings = summary.get("key_findings")
        if isinstance(findings, list):
            parts.extend(str(f).strip() for f in findings if str(f).strip())
        for key in ("methodology", "conclusions", "summary_text"):
            value = str(summary.get(key) or "").strip()
            if value:
                parts.append(value)
        text = "\n".join(parts).strip()
        if text:
            basis = ABSTRACT if summary.get("source_text") == ABSTRACT else FULL_TEXT
            return text, basis

    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        return abstract, ABSTRACT
    return "", NOTHING


def gather(items: List[Mapping], summaries: Mapping) -> Dict[str, Any]:
    """Decide what each paper contributes, before any prompt is built.

    items: reference-list rows ({"paper": {...}}). summaries: by paper_id.

    Returns the contributors with their basis and text, and the papers left
    out with the reason. Separated from prompt building so the caller can
    report on the selection and estimate its cost without committing to a run.
    """
    contributors: List[Dict[str, Any]] = []
    excluded: List[Dict[str, str]] = []

    for item in items:
        paper = item.get("paper", item)
        summary = summaries.get(paper.get("paper_id"))
        text, basis = _text_for(paper, summary)
        title = str(paper.get("title") or "(untitled)")
        if basis == NOTHING:
            excluded.append({"title": title,
                             "reason": "no summary and no abstract stored"})
            continue
        contributors.append({
            "paper_id": paper.get("paper_id"),
            "title": title,
            "authors": str(paper.get("authors") or ""),
            "pub_date": str(paper.get("pub_date") or ""),
            "basis": basis,
            "text": text,
        })

    if excluded:
        logger.info("Review: %d of %d papers cannot contribute",
                    len(excluded), len(items))
    return {"contributors": contributors, "excluded": excluded}


def build_prompt(gathered: Mapping, max_chars: int = DEFAULT_MAX_PROMPT_CHARS
                 ) -> Dict[str, Any]:
    """The synthesis prompt, and an honest account of what is in it.

    A selection can exceed the model's context window, so the prompt is capped.
    What the cap drops is returned and must be shown to the user: a synthesis
    that silently left out half its papers is a misleading document, not a
    shorter one (P2/P9).

    Papers are trimmed from the end of the list, so the order the user sees is
    the order that survives.
    """
    contributors = list(gathered.get("contributors") or [])
    if not contributors:
        return {"prompt": "", "included": [], "dropped": [],
                "excluded": list(gathered.get("excluded") or [])}

    header = "Papers:\n\n"
    blocks: List[str] = []
    included: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    used = len(header)

    for n, item in enumerate(contributors, 1):
        label = "full text" if item["basis"] == FULL_TEXT else "abstract only"
        block = (f"[{n}] {item['title']}\n"
                 f"    {item['authors']} {item['pub_date']}\n"
                 f"    (read from: {label})\n"
                 f"{item['text']}\n\n")
        if used + len(block) > max_chars and included:
            dropped.append({"title": item["title"],
                            "reason": "the combined text was too long to send"})
            continue
        used += len(block)
        blocks.append(block)
        included.append({k: item[k] for k in
                         ("paper_id", "title", "basis")})

    if dropped:
        logger.warning("Review: %d paper(s) dropped for prompt length",
                       len(dropped))

    return {
        "prompt": header + "".join(blocks),
        "included": included,
        "dropped": dropped,
        "excluded": list(gathered.get("excluded") or []),
    }


def basis_note(included: List[Mapping]) -> str:
    """One sentence on what the synthesis was actually able to read.

    Shown with the review itself, not only stored: a reader who cannot see that
    four of six papers were abstracts will read the synthesis as more grounded
    than it is.
    """
    if not included:
        return ""
    full = sum(1 for c in included if c.get("basis") == FULL_TEXT)
    abstract = len(included) - full
    summaries = f"{full} full-text summar{'y' if full == 1 else 'ies'}"
    abstracts = f"{abstract} abstract{'' if abstract == 1 else 's'}"
    if not abstract:
        return f"Based on {summaries}."
    if not full:
        return (f"Based on {abstracts} only — no full text was available for "
                f"{'that paper' if abstract == 1 else 'any of these papers'}.")
    return f"Based on {summaries} and {abstracts}."
