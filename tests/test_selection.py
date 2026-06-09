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

from src.selection import ResultsSelection, ResultsAccumulator, paper_key


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


def test_accumulator_dedups_across_batches_and_counts_total():
    """Cross-filter duplicates are dropped from the unique set but counted in total."""
    acc = ResultsAccumulator()
    f1 = [{"canonical_id": "a"}, {"canonical_id": "b"}, {"canonical_id": "c"}]
    f2 = [{"canonical_id": "b"}, {"canonical_id": "d"}]  # 'b' overlaps filter 1
    new1 = acc.add_batch(f1)
    new2 = acc.add_batch(f2)
    assert len(new1) == 3
    assert len(new2) == 1                 # only 'd' is new
    assert acc.unique_count == 4          # a, b, c, d
    assert acc.total_matches == 5         # 3 + 2 offered
    assert acc.duplicate_count == 1       # the second 'b'


def test_accumulator_reset_clears_in_place():
    """reset() empties the unique set in place so external references stay valid."""
    acc = ResultsAccumulator()
    papers_ref = acc.papers
    acc.add_batch([{"canonical_id": "a"}, {"canonical_id": "b"}])
    acc.reset()
    assert acc.unique_count == 0
    assert acc.total_matches == 0
    assert papers_ref is acc.papers       # same list object, cleared in place
    assert papers_ref == []


def test_paper_key_falls_back_when_no_canonical_id():
    """paper_key uses doi/url/title fallbacks for legacy records."""
    assert paper_key({"doi": "10.1/x"}) == "10.1/x"
    assert paper_key({"url": "http://e/x"}) == "http://e/x"
    assert paper_key({"title": "T"}) == "T"
