"""
Discover Terms: the logic behind the web route.

Purpose: Turn a natural-language description into search keywords, and parse
         the LLM's suggested terms, the same way in both front ends.
Spec:    docs/web_parity_spec_2026-09-17.md#FP1-B
Tests:   tests/web/test_discover_routes.py
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.filtering import text_group_matches


logger = logging.getLogger(__name__)


# Used only when llm_config.yaml has no discover.system_prompt; the repo
# config holds the text that is actually sent (rule 9).
FALLBACK_SYSTEM_PROMPT = (
    "You are a research librarian. Given a list of paper titles and abstracts, "
    "suggest 5 to 10 search terms of 1-3 words each that appear word for word "
    "in the titles or abstracts given. "
    'Respond with a JSON object: {"terms": ["term1", "term2", ...]}. '
    "No explanation, only the JSON object."
)

# How much of each abstract the prompt carries (the hit counts use all of it).
PROMPT_ABSTRACT_CHARS = 300


class DiscoverParseError(ValueError):
    """The LLM answered, but not with a usable {"terms": [...]} object."""


@dataclass(frozen=True)
class DiscoverSettings:
    days_back: int
    max_papers: int
    stop_words: frozenset
    system_prompt: str


def discover_settings(config: Dict[str, Any]) -> DiscoverSettings:
    """Read the `discover:` block of llm_config.yaml (repo rule 8/9)."""
    block = (config or {}).get("discover") or {}
    if not block.get("stop_words"):
        logger.warning("llm_config has no discover.stop_words — descriptions are "
                       "searched with every word, including 'the' and 'of'")
    system_prompt = str(block.get("system_prompt") or "").strip()
    if not system_prompt:
        logger.warning("llm_config has no discover.system_prompt — using the "
                       "built-in instructions")
        system_prompt = FALLBACK_SYSTEM_PROMPT
    return DiscoverSettings(
        days_back=int(block.get("days_back", 90)),
        max_papers=int(block.get("max_papers", 30)),
        stop_words=frozenset(str(w).lower() for w in block.get("stop_words", []) or []),
        system_prompt=system_prompt,
    )


def query_to_keywords(query: str, stop_words: Iterable[str]) -> str:
    """Comma-separated keywords from a description.

    The raw description as one term gets quoted into a single exact phrase
    (matches nothing); passed where a list is expected it is split into single
    letters. Splitting into meaningful words gives an OR clause that finds
    related papers.
    """
    stop = {w.lower() for w in stop_words}
    seen: set = set()
    keywords: List[str] = []
    for word in query.split():
        w = word.strip(".,;:!?\"'()")
        low = w.lower()
        if low not in stop and len(w) > 2 and low not in seen:
            seen.add(low)
            keywords.append(w)
    return ", ".join(keywords) if keywords else query.strip()


def build_discover_prompt(description: str, papers: List[Mapping[str, Any]],
                          max_papers: int) -> str:
    """The user prompt: the description, then the papers inside <papers> tags.

    Purpose: Build the Discover prompt without network or app state, with paper
             text kept apart from the instructions (standards L5, L9).
    Spec:    docs/implementation_plan_2026-10-07_discover_terms.md#DT7.C
    Tests:   tests/web/test_discover_routes.py::test_dt7c_prompt_builder_is_pure_and_delimited
    """
    parts: List[str] = []
    for p in papers[:max_papers]:
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "")[:PROMPT_ABSTRACT_CHARS].strip()
        if title:
            parts.append(f"Title: {title}")
            if abstract:
                parts.append(f"Abstract: {abstract}")
    if not parts:
        return ""
    return (f"Research interest: {description}\n\n"
            "<papers>\n" + "\n".join(parts) + "\n</papers>")


def count_term_hits(terms: Iterable[str],
                    papers: List[Mapping[str, Any]]) -> Dict[str, int]:
    """How many papers contain each term in the title or full abstract.

    Purpose: Show which suggested terms occur in the sampled papers, using the
             same match rule as a filter's "both" field.
    Spec:    docs/implementation_plan_2026-10-07_discover_terms.md#DT8.A
    Tests:   tests/web/test_discover_routes.py::test_dt8a_term_hits_counted_against_full_abstract,
             tests/web/test_discover_routes.py::test_dt8c_on_topic_phrase_absent_from_papers_counts_zero
    """
    return {t: sum(1 for p in papers
                   if text_group_matches(p, {"title": "", "abstract": "", "both": t}))
            for t in terms}


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_terms(raw: Optional[str], limit: int = 20) -> List[str]:
    """The terms list from an LLM reply. Raises DiscoverParseError when the
    reply is missing or unparsable, so a failure is not shown as "no terms"."""
    if raw is None or not str(raw).strip():
        raise DiscoverParseError("the model returned nothing — it may have timed out; see the log")
    text = str(raw).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # A sentence of prose around the object is common; take the object.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise DiscoverParseError(f"not JSON: {text[:200]!r}") from None
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise DiscoverParseError(f"not JSON: {text[:200]!r}") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("terms"), list):
        raise DiscoverParseError(f"no terms list in: {text[:200]!r}")
    return [str(t).strip()[:100] for t in obj["terms"] if str(t).strip()][:limit]
