"""
Page-independent selection model for the Search & Browse results table.

Spec:  docs/implementation_plan_2026-06-08.md#F1
Tests: tests/test_selection.py

Kept free of any Qt dependency so the logic is unit-testable under the plain
Python interpreter and stays out of the GUI widget code.
"""
from __future__ import annotations
from typing import Any, Dict, List


def paper_key(paper: Dict[str, Any]) -> str:
    """Purpose: Stable identity for a result paper, used to track selection.
    Spec:    docs/implementation_plan_2026-06-08.md#F1
    Tests:   tests/test_selection.py

    Prefers canonical_id, then doi/url/title, so selection survives re-renders
    and pagination (the table row is transient, the key is not).
    """
    return (
        paper.get("canonical_id")
        or paper.get("doi")
        or paper.get("url")
        or paper.get("source_url")
        or paper.get("title", "")
    )


class ResultsSelection:
    """Purpose: Page-independent record of which result papers are selected.
    Spec:    docs/implementation_plan_2026-06-08.md#F1
    Tests:   tests/test_selection.py

    Selection is stored by paper_key, not by table row, so 'Select All' and the
    selected-count span the entire result set regardless of the page rendered.
    """

    def __init__(self):
        self._keys: set = set()

    def select_all(self, papers: List[Dict[str, Any]]) -> None:
        self._keys = {paper_key(p) for p in papers}

    def clear(self) -> None:
        self._keys.clear()

    def set(self, key: str, selected: bool) -> None:
        if selected:
            self._keys.add(key)
        else:
            self._keys.discard(key)

    def is_selected(self, key: str) -> bool:
        return key in self._keys

    def selected(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [p for p in papers if paper_key(p) in self._keys]

    def count(self, papers: List[Dict[str, Any]]) -> int:
        return len(self.selected(papers))
