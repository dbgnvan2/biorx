"""
Discover Terms: shared logic for the desktop worker and the web route.

Purpose: Turn a natural-language description into search keywords, and parse
         the LLM's suggested terms, the same way in both front ends.
Spec:    docs/web_parity_spec_2026-09-17.md#FP1-B
Tests:   tests/web/test_discover_routes.py
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


class DiscoverParseError(ValueError):
    """The LLM answered, but not with a usable {"terms": [...]} object."""


@dataclass(frozen=True)
class DiscoverSettings:
    days_back: int
    max_papers: int
    stop_words: frozenset


def discover_settings(config: Dict[str, Any]) -> DiscoverSettings:
    """Read the `discover:` block of llm_config.yaml (repo rule 8/9)."""
    block = (config or {}).get("discover") or {}
    return DiscoverSettings(
        days_back=int(block.get("days_back", 90)),
        max_papers=int(block.get("max_papers", 30)),
        stop_words=frozenset(str(w).lower() for w in block.get("stop_words", []) or []),
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
