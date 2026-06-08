"""
Tests for F1 — 'Select All' must span every page of results.

Spec: docs/implementation_plan_2026-06-08.md#F1

ResultsSelection is pure Python (no Qt), so these run under the canonical
pytest interpreter. They exercise the page-independent selection model that
backs the Search & Browse results table.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.selection import ResultsSelection, paper_key


def _papers(n: int):
    """n papers with stable canonical_ids spanning more than one 20-row page."""
    return [{"canonical_id": f"cid-{i}", "title": f"Paper {i}"} for i in range(n)]


def test_f1_1_select_all_spans_all_results():
    """F1.1: Select All marks every paper, not just one page's worth."""
    papers = _papers(45)  # 3 pages at 20/page
    sel = ResultsSelection()
    sel.select_all(papers)
    assert sel.count(papers) == 45
    assert len(sel.selected(papers)) == 45


def test_f1_2_selection_is_page_independent():
    """F1.2: membership is decided by key, not by which rows are rendered."""
    papers = _papers(45)
    sel = ResultsSelection()
    # Select two papers that live on different pages (index 0 and index 40).
    sel.set(paper_key(papers[0]), True)
    sel.set(paper_key(papers[40]), True)
    # Simulating any page slice never changes the model.
    page1 = papers[0:20]
    page3 = papers[40:45]
    assert sel.count(papers) == 2
    assert sel.selected(page1) == [papers[0]]
    assert sel.selected(page3) == [papers[40]]


def test_f1_3_selected_returns_full_set():
    """F1.3: selected() returns the chosen papers across the whole result set."""
    papers = _papers(30)
    sel = ResultsSelection()
    sel.select_all(papers)
    selected = sel.selected(papers)
    assert selected == papers  # identity/order preserved, spans both pages


def test_f1_4_toggle_add_remove():
    """F1.4: per-paper toggle adds then removes, deduped by key."""
    papers = _papers(3)
    sel = ResultsSelection()
    k = paper_key(papers[1])
    assert sel.count(papers) == 0
    sel.set(k, True)
    sel.set(k, True)  # idempotent add
    assert sel.count(papers) == 1
    sel.set(k, False)
    assert sel.count(papers) == 0


def test_f1_5_is_selected_drives_checkbox_state():
    """F1.5: rendering can decide a row's check state purely from the model."""
    papers = _papers(25)
    sel = ResultsSelection()
    sel.set(paper_key(papers[22]), True)  # a paper on page 2
    assert sel.is_selected(paper_key(papers[22])) is True
    assert sel.is_selected(paper_key(papers[0])) is False


def test_f1_clear_resets_selection():
    """New-search reset: clear() empties the selection."""
    papers = _papers(10)
    sel = ResultsSelection()
    sel.select_all(papers)
    sel.clear()
    assert sel.count(papers) == 0


def test_paper_key_falls_back_when_no_canonical_id():
    """paper_key uses doi/url/title fallbacks for legacy records."""
    assert paper_key({"doi": "10.1/x"}) == "10.1/x"
    assert paper_key({"url": "http://e/x"}) == "http://e/x"
    assert paper_key({"title": "T"}) == "T"
