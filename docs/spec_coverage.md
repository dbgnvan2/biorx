# Spec Coverage — Search panel enhancements

Specs: `docs/implementation_plan_2026-06-07.md` (E1/E2),
`docs/implementation_plan_2026-06-08.md` (F1/B1)

## F1 + B1 — "Select All" across all pages

| Spec ID | Description | Implementation | Test | Status |
|---|---|---|---|---|
| F1.1 | Select All spans every page, not just rendered rows | `src/selection.py::ResultsSelection.select_all`; `gui.py::MainWindow._select_all` | `tests/test_selection.py::test_f1_1_select_all_spans_all_results` | done |
| F1.2 | Selection is page-independent (persists across Prev/Next) | `src/selection.py::ResultsSelection` (keyed by `paper_key`); `gui.py::display_page` reads model | `tests/test_selection.py::test_f1_2_selection_is_page_independent` | done |
| F1.3 | Checked-papers returns the full selected set | `gui.py::MainWindow._checked_papers` → `ResultsSelection.selected` | `tests/test_selection.py::test_f1_3_selected_returns_full_set` | done |
| F1.4 | Per-paper toggle add/remove, deduped by key | `gui.py::MainWindow._on_item_changed` → `ResultsSelection.set` | `tests/test_selection.py::test_f1_4_toggle_add_remove` | done |
| F1.5 | Paginated rows are checkable + reflect model (latent bug A) | `gui.py::display_page` (sets `ItemIsUserCheckable` + check state from model) | `tests/test_selection.py::test_f1_5_is_selected_drives_checkbox_state` | done |
| B1 | Single rendering path: live search + Prev/Next both paginate | `gui.py::_append_batch` renders via `display_page` | covered by F1.* (model) + human verify | done |
| B1b | Cross-filter dedup + Total/Unduplicated counts shown | `src/selection.py::ResultsAccumulator`; `gui.py::_append_batch`, `_update_matches_label`, `matches_label` | `tests/test_selection.py::test_accumulator_*` | done |
| F1.6 | End-to-end GUI behaviour | n/a (GUI runtime) | **human verification** — checklist below | pending user check |

### F1/B1 human-verification checklist (run `python gui.py`)

1. Run a search returning > 20 papers (multiple pages).
2. Click **Select All** → the "N selected" count equals the **total** count, not 20.
3. Page **Next/Prev** → every page shows its rows checked; the count stays at the total.
4. Uncheck one paper on page 2, page away and back → it stays unchecked; count is total−1.
5. Save selection as a reference list → all selected papers across pages are saved.
6. Start a new search → selection resets to 0.

---

# Spec Coverage — Search panel enhancements (2026-06-07 plan)

Spec: `docs/implementation_plan_2026-06-07.md`

| Spec ID | Description | Implementation | Test | Status |
|---|---|---|---|---|
| E1.1 | Saved-filter rows start unchecked on startup | `gui.py::filter_initial_check_state`, applied in `gui.py::MainWindow.load_filters_list` | `tests/test_gui_filters.py::test_e1_1_filter_initial_check_state_is_unchecked`, `::test_e1_1_filters_unchecked_on_startup` | done |
| E1.2 | `enabled` field still drives "Run All Enabled" | `gui.py::filter_is_enabled`, used in `gui.py::MainWindow.run_all_enabled` | `tests/test_gui_filters.py::test_e1_2_run_all_enabled_uses_enabled_field` | done |
| E2.1 | Orchestrator emits per-source status via `on_status` | `src/sources/orchestrator.py::SourceOrchestrator.search` (+ `_source_label`) | `tests/test_orchestrator.py::test_e2_1_on_status_emits_per_source` | done |
| E2.2 | Real progress total surfaced (EuropePMC/PubMed `hitCount`, bioRxiv `_total`) | `src/sources/europepmc.py::EuropePmcAdapter.search` (`last_total`); `orchestrator.py::search`/`_search_source` cumulative totals | `tests/test_adapters.py::test_e2_2_europepmc_exposes_last_total`, `tests/test_orchestrator.py::test_e2_2_progress_reports_known_total` | done |
| E2.3 | Enrichment phase emits status + progress | `src/sources/orchestrator.py::SourceOrchestrator._enrich` | `tests/test_orchestrator.py::test_e2_3_enrichment_emits_status` | done |
| E2.4 | GUI forwards `on_status`; shows current source/phase | `gui.py::SearchWorker.phase` signal + `run`; `gui.py::MainWindow._on_phase`, `_search_status_text` | line evidence + human verify (E2.7) | done |
| E2.5 | GUI shows "Enriching N papers…" with determinate bar | `gui.py::MainWindow._on_phase` (sets `_progress_format`), `_update_progress` | line evidence + human verify (E2.7) | done |
| E2.6 | Clear completion: bar tops out + "Done — N papers found" | `gui.py::MainWindow._on_all_filters_done` | line evidence + human verify (E2.7) | done |
| E2.7 | End-to-end visual behaviour (label/bar/completion) | n/a (GUI runtime) | **human verification** — checklist below | pending user check |

## Test execution

- Canonical suite (`/opt/homebrew/bin/pytest tests/ -q`): **67 passed, 1 skipped**.
  The skip is `tests/test_gui_filters.py` — the homebrew pytest interpreter
  (Python 3.11) has no PyQt6, so the module skips via `importorskip`.
- PyQt6-capable interpreter
  (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest`,
  `QT_QPA_PLATFORM=offscreen`): GUI + orchestrator + adapter tests **21 passed**.
  `import gui` succeeds under this interpreter.

## E2.7 human-verification checklist (run `python gui.py`)

Run a filter with a large result set (e.g. the "Multilevel Selection" query from the log) and confirm:

1. Status label shows per-source phases, e.g. `⏳  Searching Europe PMC… · N so far`,
   then `Europe PMC: 999 fetched`, then the next source.
2. The progress bar **advances** against a real total (no longer pinned full).
3. After fetching, an `Enriching N papers…` phase appears with a moving bar
   (instead of an apparent freeze).
4. On completion the label reads `✅  Done — N papers found` and the bar hides.
5. On startup, all Saved Filters checkboxes are **unchecked**.
