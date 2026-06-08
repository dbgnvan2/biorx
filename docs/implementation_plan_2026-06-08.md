# Implementation Plan — "Select All" across all pages (2026-06-08)

## Source requirement (verbatim from user)

> The Select All button under the list of "papers found" only selects the page that is
> showing — it should select everything across all pages.

ID assigned for traceability: **F1**.

---

## Diagnosis (confirmed in code)

The Search & Browse results table (`results_table`) is paginated: Prev/Next call
`display_page()` (`gui.py:1238`) which rebuilds the table to a 20-row slice
(`results_per_page = 20`, `gui.py:925`). **Selection state lives only in the per-row
`QTableWidgetItem` check state**, so it is scoped to the currently-rendered rows.

| # | Problem | Location |
|---|---|---|
| Root cause | `_select_all` iterates `results_table.rowCount()` — only the rendered rows | `gui.py:1285-1292` |
| Same scope bug | `_checked_papers` and `_update_checked_count` only read rendered rows | `gui.py:1278-1283`, `1303-1309` |
| Latent bug A | `display_page` rebuilds rows **without** `ItemIsUserCheckable` / `setCheckState` — paginated rows have **no checkbox at all** | `gui.py:1243-1254` |
| Latent bug B | Check state is destroyed on every page change (table is rebuilt) — selection cannot persist across pages | `gui.py:1239` |

The fix must move selection out of the widget and into a data model keyed by a stable
paper identifier, so Select All / count / "checked papers" all span the full result set
regardless of which page is rendered.

Every paper dict exposes a stable `canonical_id` (`src/sources/schema.py:88`), with `doi`
and `url` as fallbacks.

---

## Design

Add a small **pure-Python** selection model (no Qt dependency) plus a `_paper_key` helper,
both in `gui.py` near the existing `filter_*` helpers:

```python
def paper_key(paper: dict) -> str:
    return paper.get("canonical_id") or paper.get("doi") or paper.get("url") or paper.get("title", "")

class ResultsSelection:
    """Tracks which result papers are selected, independent of the paginated view."""
    def __init__(self): self._keys = set()
    def select_all(self, papers): self._keys = {paper_key(p) for p in papers}
    def clear(self): self._keys.clear()
    def set(self, key, on): (self._keys.add(key) if on else self._keys.discard(key))
    def is_selected(self, key) -> bool: return key in self._keys
    def selected(self, papers) -> list: return [p for p in papers if paper_key(p) in self._keys]
    def count(self, papers) -> int: return len(self.selected(papers))
```

`MainWindow` (Search & Browse tab) holds `self._selection = ResultsSelection()` and:

- `_select_all` → `self._selection.select_all(self.current_results)`, then refresh visible
  checkboxes + count.
- `_select_none` → `self._selection.clear()`, then refresh.
- `_on_item_changed` → `self._selection.set(paper_key(paper), checked)`, then refresh count.
- `_append_batch` and `display_page` → set each row's checkbox from
  `self._selection.is_selected(key)`; both make the title cell checkable and toggle
  `itemChanged` signals off while populating (matching the existing `_select_all` pattern).
- `_checked_papers` → `self._selection.selected(self.current_results)`.
- `_update_checked_count` → `self._selection.count(self.current_results)`.
- Reset `self._selection.clear()` at the start of `_run_filters` (new search clears selection).

This also fixes latent bugs A and B as a direct consequence (paginated rows become
checkable and selection persists across pages), which is required for F1 to be correct.

---

## Acceptance criteria → verifying tests

- **F1.1** Select All selects **every** paper in `current_results`, not just rendered rows.
  - **Test:** `tests/test_selection.py::test_f1_1_select_all_spans_all_results` — build
    `ResultsSelection`, `select_all` over 45 papers, assert `count == 45` and
    `len(selected) == 45`.
- **F1.2** Selection persists across page navigation (model is page-independent).
  - **Test:** `tests/test_selection.py::test_f1_2_selection_is_page_independent` — select a
    subset by key, assert membership is unchanged after simulating a page slice change
    (the model never sees the table).
- **F1.3** `_checked_papers` returns selected papers from the whole result set.
  - **Test:** `tests/test_selection.py::test_f1_3_selected_returns_full_set` — select all,
    assert `selected(papers)` returns all dicts (by identity), across a >1-page set.
- **F1.4** Per-paper toggle add/remove works and dedupes by key.
  - **Test:** `tests/test_selection.py::test_f1_4_toggle_add_remove` — `set(k, True)` twice
    then `set(k, False)`; assert count transitions 0→1→0.
- **F1.5** Paginated rows are checkable and reflect model state (regression for latent bug A).
  - **Test (offscreen Qt):** `tests/test_gui_filters.py` companion or
    `tests/test_selection.py::test_f1_5_render_checkbox_from_model` — verify a helper that
    decides a row's `Qt.CheckState` from `ResultsSelection.is_selected` returns Checked for a
    selected key and Unchecked otherwise. (Pure helper; no MainWindow construction.)
- **F1.6** *(not unit-testable — flagged)* End-to-end GUI behaviour: run a multi-page search,
  click Select All, page Next/Prev, confirm count stays at total and all pages show checks.
  **Human verification** checklist added to `docs/spec_coverage.md`.

All `tests/test_selection.py` tests are pure Python (no Qt) → run under the canonical
`/opt/homebrew/bin/pytest`.

---

## Order of implementation

1. Add `paper_key` + `ResultsSelection` to `gui.py`; add `tests/test_selection.py`; run tests.
2. Wire the Search & Browse tab methods to the model (`_select_all`, `_select_none`,
   `_on_item_changed`, `_append_batch`, `display_page`, `_checked_papers`,
   `_update_checked_count`, `_run_filters` reset).
3. Smoke-import `gui.py` under the PyQt6 interpreter; run full suite both interpreters.
4. Update `docs/spec_coverage.md` (append F1 rows + human-verification checklist).

---

## Adjacent issues found, not fixed (workflow rule 8)

- **B1 — Dual rendering of `results_table`.** `_append_batch` (`gui.py:1180`) appends *all*
  matched rows during a live search (no pagination), while `display_page` (`gui.py:1238`)
  renders a 20-row slice. After a search the table holds everything; clicking Next switches to
  paginated mode. The selection-model fix makes Select All correct under *both*, but the
  inconsistent rendering itself is left as-is. Worth a follow-up to make live search paginate
  too (or drop pagination and rely on a scroll view).
- **B2 — Second tab `_select_all` (`gui.py:2054`, reference-list browser).** Currently correct
  because `_load_papers` loads all rows unpaginated, but it uses the same widget-scoped pattern;
  if that table ever gets pagination it will exhibit the same bug. Not changed here.
