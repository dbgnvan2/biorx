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
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import requests

from src.filtering import split_terms, wildcard_pattern
from src.search_terms import and_parts, normalise_text
from src.sources.base import with_retry
from src.sources.errors import SourceUnavailableError
from src.sources.europepmc import BASE_URL as EUROPEPMC_URL
from src.sources.query_builder import build_europepmc_query


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

# Used only when llm_config.yaml has no discover.replace_prompt. {terms} is
# replaced with the terms that found nothing.
FALLBACK_REPLACE_PROMPT = (
    "These terms found no papers when searched: {terms}. A multi-word term is "
    "searched as an exact phrase. Replace each with a term of 1-3 words that "
    "appears word for word in the titles or abstracts above. "
    'Respond with a JSON object: {"terms": ["term1", ...]}. No explanation.'
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
    replace_prompt: str = FALLBACK_REPLACE_PROMPT
    check_timeout_s: float = 15.0
    check_delay_s: float = 0.2


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
    replace_prompt = str(block.get("replace_prompt") or "").strip()
    if "{terms}" not in replace_prompt:
        logger.warning("llm_config has no discover.replace_prompt with {terms} — "
                       "using the built-in instructions")
        replace_prompt = FALLBACK_REPLACE_PROMPT
    if "check_timeout_s" not in block or "check_delay_s" not in block:
        logger.warning("llm_config discover block has no check_timeout_s / "
                       "check_delay_s — using 15 s and 0.2 s")
    return DiscoverSettings(
        days_back=int(block.get("days_back", 90)),
        max_papers=int(block.get("max_papers", 30)),
        stop_words=frozenset(str(w).lower() for w in block.get("stop_words", []) or []),
        system_prompt=system_prompt,
        replace_prompt=replace_prompt,
        check_timeout_s=float(block.get("check_timeout_s", 15.0)),
        check_delay_s=float(block.get("check_delay_s", 0.2)),
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
                          max_papers: int) -> Tuple[str, List[Mapping[str, Any]]]:
    """The user prompt (the description, then the papers inside <papers> tags)
    and the papers it included. A paper with no title is left out, so the hit
    counts are taken over the included list, not the papers found.

    Purpose: Build the Discover prompt without network or app state, with paper
             text kept apart from the instructions (standards L5, L9).
    Spec:    docs/implementation_plan_2026-10-07_discover_terms.md#DT7.C
    Tests:   tests/web/test_discover_routes.py::test_dt7c_prompt_builder_is_pure_and_delimited
    """
    parts: List[str] = []
    included: List[Mapping[str, Any]] = []
    for p in papers[:max_papers]:
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "")[:PROMPT_ABSTRACT_CHARS].strip()
        if title:
            included.append(p)
            parts.append(f"Title: {title}")
            if abstract:
                parts.append(f"Abstract: {abstract}")
    if not parts:
        return "", []
    return (f"Research interest: {description}\n\n"
            "<papers>\n" + "\n".join(parts) + "\n</papers>"), included


def _whole_word_pattern(term: str) -> "re.Pattern[str]":
    """A term as whole words: "aging" does not match "imaging". A trailing *
    is a prefix of a word, as in the filter ("adolescen*")."""
    if term.endswith("*"):
        return wildcard_pattern(term)
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")


def count_term_hits(terms: Iterable[str],
                    papers: List[Mapping[str, Any]]) -> Dict[str, int]:
    """How many papers contain each term, as whole words, in the title or the
    full abstract. Commas split a term into alternatives and AND joins parts
    that must all appear, as in a filter.

    The filter's own matcher (src/filtering.py match_term) is a plain
    substring test, so "aging" would count every paper about imaging; the
    sources search words, so this count uses whole words to say what a search
    is likely to find.

    Purpose: Show which suggested terms occur in the sampled papers.
    Spec:    docs/implementation_plan_2026-10-07_discover_terms.md#DT8.A
    Tests:   tests/web/test_discover_routes.py::test_dt8a_term_hits_counted_against_full_abstract,
             tests/web/test_discover_routes.py::test_dt8c_on_topic_phrase_absent_from_papers_counts_zero,
             tests/web/test_discover_routes.py::test_dt8c_short_term_inside_a_longer_word_counts_zero,
             tests/web/test_discover_routes.py::test_and6_and_term_counts_need_every_part
    """
    # Title and abstract kept apart (a phrase does not span them, TD5),
    # hyphens read as spaces (TD4) — the filter's rules.
    papers_fields = [(normalise_text(p.get("title") or ""), normalise_text(p.get("abstract") or ""))
                     for p in papers]
    hits: Dict[str, int] = {}
    for term in terms:
        # Commas: any alternative; AND inside one: every part (AND6).
        alternatives = [[_whole_word_pattern(normalise_text(p)) for p in and_parts(t)]
                        for t in split_terms(term)]
        hits[term] = sum(1 for fields in papers_fields
                         if any(alt and all(any(pt.search(f) for f in fields) for pt in alt)
                                for alt in alternatives))
    return hits


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


# ── DT9: offer only terms that find papers ────────────────────────────────────
# docs/implementation_plan_2026-10-07_discover_terms_verified.md

def term_filter(term: str, days_back: int) -> Dict[str, Any]:
    """The filter a click on this term creates, as far as the search sees it:
    the term as a "both" group over the Discover window, no other limits."""
    return {"text_groups": [{"title": "", "abstract": "", "both": term}],
            "days_back": days_back}


def europepmc_term_count(term: str, days_back: int, get: Callable[..., Any],
                         timeout: float) -> Optional[int]:
    """Papers Europe PMC returns for this term, or None when it could not say.

    Purpose: Check a suggested term with the query a filter made from it sends.
             A failure is None, never 0, so an outage does not drop good
             terms (P1) — the adapter's get_total returns 0 on failure.
    Spec:    docs/implementation_plan_2026-10-07_discover_terms_verified.md#DT9.A
    Tests:   tests/web/test_discover_routes.py::test_dt9a_check_uses_the_filters_own_query,
             tests/web/test_discover_routes.py::test_dt9a2_failed_count_is_none_not_zero
    """
    params = {"query": build_europepmc_query(term_filter(term, days_back)),
              "resultType": "idlist", "pageSize": 1, "format": "json"}
    try:
        resp = with_retry(lambda: get(EUROPEPMC_URL, params=params, timeout=timeout),
                          source_label="Europe PMC term check")
    except (SourceUnavailableError, requests.RequestException) as e:
        logger.warning("Discover: could not check %r in Europe PMC: %s", term, e)
        return None
    if resp.status_code != 200:
        logger.warning("Discover: Europe PMC answered %s checking %r", resp.status_code, term)
        return None
    try:
        return int(resp.json()["hitCount"])
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("Discover: unusable Europe PMC reply checking %r: %s", term, e)
        return None


def check_terms(terms: List[str], count: Callable[[str], Optional[int]],
                delay_s: float,
                on_progress: Callable[[int, int], None] = lambda *_: None
                ) -> Dict[str, Optional[int]]:
    """Count each term, one request at a time with a gap between (rate limit).

    Purpose: The live count for every term, in order.
    Spec:    docs/implementation_plan_2026-10-07_discover_terms_verified.md#DT9.A
    Tests:   tests/web/test_discover_routes.py::test_dt9f_phase_names_the_check
    """
    counts: Dict[str, Optional[int]] = {}
    for i, term in enumerate(terms):
        on_progress(i + 1, len(terms))
        if i and delay_s > 0:
            time.sleep(delay_s)
        counts[term] = count(term)
    return counts


def build_replace_prompt(first_prompt: str, failed: List[str], template: str) -> str:
    """The first prompt (the papers again — the model keeps no memory) with
    the request to replace the terms that found nothing.

    Purpose: Ask once for replacements of zero-hit terms.
    Spec:    docs/implementation_plan_2026-10-07_discover_terms_verified.md#DT9.B
    Tests:   tests/web/test_discover_routes.py::test_dt9b_zero_terms_are_replaced_once
    """
    listed = "; ".join(f'"{t}"' for t in failed)
    return f"{first_prompt}\n\n{template.replace('{terms}', listed)}"
