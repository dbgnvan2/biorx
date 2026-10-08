"""
The AND operator inside one search term.

Purpose: One reading of "a AND b" for the local filter, every source's query
         and the Discover counts, so they cannot disagree.
Spec:    docs/implementation_plan_2026-10-07_and_terms.md#AND1
Tests:   tests/test_search_terms.py::test_and1_and_parts

Commas separate alternatives (OR) and are split first, by the callers.
Within one term, uppercase AND with whitespace (or the term's edge) on each
side joins parts that must all appear. Lowercase "and" is part of a phrase
("anxiety and depression"), and AND inside a word ("BRANDING") is a word.
"""

from __future__ import annotations

import re
from typing import List

_AND = re.compile(r"(?<!\S)AND(?!\S)")


def and_parts(term: str) -> List[str]:
    """The parts of a term that must all match; [term] when it has no AND,
    [] when nothing is left (a term that is only "AND")."""
    return [p.strip() for p in _AND.split(term or "") if p.strip()]


# Punctuation, hyphens included, is matched as a space, so "kin selection"
# matches "kin-selection" and "COVID-19: outcomes" matches "COVID-19
# outcomes", as Europe PMC matches them (TD4; the colon case was found in the
# TD live check: 294 Europe PMC hits, 0 kept). "*" is kept for wildcards.
_PUNCTUATION = re.compile(r"[^\w\s*]")
_SPACES = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Lowercase, punctuation (hyphens included) as spaces, runs of spaces as
    one — applied to both the text and the term before matching.

    Purpose: "kin selection" matches "kin-selection" (TD4).
    Spec:    docs/implementation_plan_2026-10-07_gate_todos.md#TD4
    Tests:   tests/test_filtering.py::test_td4_hyphen_matches_space
    """
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", (text or "").lower())).strip()
