# Implementation Plan — Search panel enhancements (2026-06-07)

## Source requirements (verbatim from user)

1. **E1** — "Saved Filters shown in the Search Browse panel should be unchecked on startup."
2. **E2** — "The Search Status should provide more details than 'quick' — there is a progress
   bar that doesn't show anything, there is no indication that the search is over."

These were informal requirements; IDs (E1, E2, and sub-IDs) are assigned here for traceability.

---

## Diagnosis (root causes confirmed in code)

| Symptom | Root cause | Location |
|---|---|---|
| Filters checked on startup | Check state derived from filter's `enabled` field | `gui.py:1024` |
| Progress bar "doesn't show anything" | Orchestrator emits `on_progress(fetched, fetched)` — value always == max, so the bar sits at 100% and never advances; no real total is surfaced for EuropePMC/PubMed | `orchestrator.py:251` |
| EuropePMC/PubMed total never used | `hitCount` is read from the API response but discarded | `europepmc.py:145–154` |
| "no indication search is over" / apparent hang | After all sources fetch (~2000 records in the user's log), `_enrich()` runs Crossref + Unpaywall **synchronously per DOI** with **no** progress/status callback — a multi-minute silent phase before `finished` fires | `orchestrator.py:271–285` |
| Status shows "quick" | `quick_search` names the filter `"quick"`; status text shows the filter name and never the source/phase currently running | `gui.py:1166–1185`, `_search_status_text` `gui.py:1114` |

---

## Acceptance criteria → verifying tests

### E1 — Saved Filters unchecked on startup

- **E1.1** `load_filters_list()` sets every list item's check state to `Qt.CheckState.Unchecked`
  on load, regardless of the filter's `enabled` field.
  - **Test (automated):** `tests/test_gui_filters.py::test_e1_1_filters_unchecked_on_startup`
    — run under `QT_QPA_PLATFORM=offscreen`; build the `QListWidget`-population logic via a
    small extracted helper and assert all items are `Unchecked`. (See "Refactor for testability"
    below — the check-state decision is extracted into a pure function so no full `MainWindow`
    is constructed.)
  - **Fallback evidence if offscreen Qt is unavailable on this machine:** specific line in
    `gui.py` showing the unconditional `Qt.CheckState.Unchecked`, plus human verification
    (launch app → filters appear unchecked).

- **E1.2** The `enabled` field still drives **"Run All Enabled"** (`run_all_enabled`,
  `gui.py:1039`) and the Configure tab — i.e. unchecking the visual boxes does **not** change
  which filters are considered "enabled".
  - **Test (automated):** `tests/test_gui_filters.py::test_e1_2_run_all_enabled_uses_enabled_field`
    — assert the selection logic for "Run All Enabled" reads the `enabled` field, not check state.

### E2 — Search status detail, working progress bar, completion signal

- **E2.1** `SourceOrchestrator.search()` accepts a new optional `on_status: Callable[[str], None]`
  callback and emits a human-readable phase string **before each source** is queried
  (e.g. `"Searching Europe PMC…"`) and **after** it completes (e.g. `"Europe PMC: 999 fetched"`).
  - **Test:** `tests/test_orchestrator.py::test_e2_1_on_status_emits_per_source` — capture
    `on_status` calls with MagicMock adapters; assert a "Searching …" string per active source.

- **E2.2** The orchestrator surfaces a meaningful running **total** so the progress bar advances.
  - EuropePMC/PubMed: capture `hitCount` from the existing search response (no extra request) and
    expose it as `adapter.last_total`; orchestrator reports `on_progress(fetched, known_total)`
    where `known_total` is the cumulative known total across sources (bounded by the fetch cap).
  - **Test:** `tests/test_orchestrator.py::test_e2_2_progress_reports_known_total` — fake adapter
    with `last_total = 120`; assert `on_progress` is eventually called with `total > fetched`
    (i.e. not the degenerate `fetched == total` every time).
  - **Test:** `tests/test_adapters.py::test_e2_2_europepmc_exposes_last_total` — mock the HTTP
    response with `hitCount`; assert `adapter.last_total` is set after `search()`.

- **E2.3** The enrichment phase emits progress so the UI is not silent: `on_status("Enriching N
  papers…")` before the loop and `on_progress` updates every ~25 records during enrichment.
  - **Test:** `tests/test_orchestrator.py::test_e2_3_enrichment_emits_status` — enable a mock
    Crossref; assert `on_status` is called with an "Enriching" message during `search()`.

- **E2.4** `SearchWorker` forwards `on_status` to its existing `status` signal; the GUI shows the
  current source/phase in the status label (so a quick search shows e.g.
  `⏳  Searching Europe PMC… · 42 so far` rather than just `'quick'`).
  - **Evidence:** specific lines in `gui.py` (`SearchWorker.run` wires `on_status=`; status label
    reflects it). GUI-thread display flagged for **human verification** (see E2.7).

- **E2.5** During enrichment the status label shows `"Enriching N papers…"` and the progress bar
  switches to a determinate enrichment range, so the post-fetch phase is visibly active.
  - **Evidence:** specific lines in `gui.py`; **human verification** (E2.7).

- **E2.6** On completion the status clearly reads `Done — N papers found` and the progress bar is
  hidden/reset (existing `_on_all_filters_done`, `gui.py:1157` — verify it still fires correctly
  after the enrichment-progress changes; add a final `on_progress(total, total)` so the bar tops
  out before hiding).
  - **Evidence:** specific line `gui.py:1162`; **human verification** (E2.7).

- **E2.7** *(not code-testable — flagged)* End-to-end visual behaviour of the GUI (label text,
  bar advancing, completion). **Human verification:** run `python gui.py`, run a filter with a
  large result set (e.g. the "Multilevel Selection" query from the log), and confirm: (a) per-source
  status text appears, (b) the bar advances rather than sitting full, (c) an "Enriching…" phase is
  shown, (d) a clear "Done — N papers found" appears at the end. A short checklist will be added to
  the status report.

---

## Refactor for testability (E1)

To make E1.1/E1.2 automatable without constructing the whole `MainWindow`, extract the check-state
and enabled-selection decisions into module-level pure helpers in `gui.py`:

- `filter_initial_check_state() -> Qt.CheckState` → always returns `Unchecked`.
- `filter_is_enabled(f: dict) -> bool` → returns `f.get("enabled", True)`.

`load_filters_list`, `run_all_enabled`, and the Configure tab call these helpers. Tests import the
helpers directly. This keeps editorial/logic decisions out of inline widget code (consistent with
the project's "logic in testable units" practices).

---

## Order of implementation (dependencies called out)

1. **E1** (independent, smallest): extract helpers, change check-state to Unchecked, add
   `tests/test_gui_filters.py`. Run tests.
2. **E2.2 adapter total** (`europepmc.py` `last_total`) — prerequisite for E2.2 orchestrator work.
3. **E2.1 + E2.2 + E2.3 orchestrator** (`orchestrator.py`): add `on_status`, real totals,
   enrichment progress. Add orchestrator/adapter tests. Run tests.
4. **E2.4–E2.6 GUI wiring** (`gui.py`, `SearchWorker` + slots): forward `on_status`, enrichment
   display, completion top-out. Depends on step 3's new callback signature.
5. Full `pytest tests/ -v`, then human-verification checklist (E2.7), then status report +
   `docs/spec_coverage.md`.

---

## Adjacent issues found, not fixed (per workflow rule 8)

These exhibit related problems but are **out of scope** for this change — listed for your decision:

- **A1 — Enrichment is O(records) synchronous HTTP with no concurrency/caching at the call site.**
  `orchestrator._enrich()` (`orchestrator.py:271`) makes up to 2 network calls per DOI serially.
  For the ~2000-record run in the log this is the multi-minute stall. E2.3 makes it *visible* but
  does not make it *faster*. A follow-up could batch/parallelise or short-circuit via `cache.py`.
- **A2 — `MAX_PAPERS = 2000` (gui) vs `max_results=2000` (orchestrator) vs per-source 1000 cap**
  are spread across files as magic numbers; the user's log shows EuropePMC alone hitting 999 and
  PubMed 1000, so the global cap is effectively reached before later sources. Worth centralising.
- **A3 — `quick_search` sets `self.status_label.setText("Searching…")` then `_run_filters`
  immediately overwrites it** (`gui.py:1184`); dead line. Trivial, not fixed here.

---

## Status report & coverage

On completion this plan will produce `docs/spec_coverage.md` with the
`| Spec ID | Description | Implementation | Test | Status |` table, plus the per-criterion status
report, including the E2.7 human-verification checklist results.
