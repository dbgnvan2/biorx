# Implementation plan — running a saved filter (web app)

**Request:** chat, 2026-09-18 (four items, quoted below).
**Status:** IMPLEMENTED 2026-09-18 (6dc5120, e734ef4, babb276, dae973f). Verified in the real web UI the same day.
**Surface:** the web app (`web/`), Search tab → saved filters → **Run**. The desktop GUI is
touched only where the fix is shared code.

---

## 0. Spec IDs

| ID | Verbatim request |
|---|---|
| FR1 | "I have a filter with nothing in it and you ran it anyway - that shouldn't happen." |
| FR2 | "I run a filter, it "enriches" M of N papers and then says nothing matched. How do you get N papers to start with? what does "enrich" do." |
| FR3 | "show the match count as you go. found/enriched/Match progress indication" |
| FR4 | "When I hit "run" on a filter, the button should say "Running" and when it's completed say Done. Clicking on Done or Run runs that filter." |

---

## 1. Findings (what the code does today)

**FR1.** The desktop GUI already refuses empty filters (`gui.py:843`, using
`filter_has_text` in `src/filters_store.py:48`). The web routes never call it:
`POST /api/searches` (`web/routes_searches.py:141`) and `POST /api/filters/{id}/test`
(`web/routes_filters.py:77`) queue the job whatever the filter holds. With no terms, every
source is queried for its whole date window and nothing is filtered out.
`agents/monitor.py` (scheduled CLI runs) has the same gap.

**FR2.** How N is reached:
1. Each source (Europe PMC, PubMed, PsyArXiv, SocArXiv, bioRxiv/medRxiv, arXiv, …) gets a
   query built from the filter. These queries are approximate — each API has its own syntax,
   and several facets have no API equivalent.
2. Pages of 50 are fetched per source until the source runs out, 20 pages, or the run's
   **Max results** (default 200) is used up. Duplicates across sources are merged.
   That unique set is N ("fetched").
3. As each page arrives, the full filter is re-applied locally (`src/filtering.py`). Only
   those papers become results. This is where "nothing matched" comes from: the sources'
   loose matches did not pass the exact filter.

What "enrich" does: for every fetched paper **that has a DOI**, two HTTP calls — Crossref
(fills missing title/abstract/licence/date) and Unpaywall (finds an open-access PDF link).
M of N in the status is "M of the papers with a DOI done so far".

**Defects found while tracing FR2:**
- **D-a. Enrichment runs on every fetched paper, not the matched ones.** A run that matched
  nothing still made up to 2 × N HTTP calls. (`src/sources/orchestrator.py:246`)
- **D-b. Enrichment never reaches the results.** The web job (and the GUI) snapshot each
  paper with `to_dict()` when its page arrives, *before* enrichment. Enrichment then edits
  the record objects, which nothing reads. So the enrichment phase currently costs time
  and changes nothing the user sees. (`web/routes_searches.py:109`, `gui.py:147`)
- **D-c. "N were fetched" is wrong after enrichment.** The enrichment phase reports
  progress through the same `on_progress` callback, which overwrites `job.fetched` with the
  enrichment count. The "No papers matched … N were fetched" message then shows the DOI
  count, not the fetched count. (`web/routes_searches.py:116`)

**FR3.** `job.matched` is already kept up to date on the server and returned by the poll,
but the page shows only the phase text (`web/static/app.js:672`).

**FR4.** Each saved filter's Run button (`web/static/app.js:603`) never changes and stays
clickable during a run; a second click starts a second job while the first keeps running
server-side.

---

## 2. Changes

**C1 (FR1).** Add `filter_has_criteria(f)` to `src/filters_store.py`: true when the
normalised filter has a text term, an author, or an institution — the same rule as today's
`filter_has_text`, but applied after `normalise_filter` so legacy web-saved `keywords`
groups count. `filter_has_text` becomes an alias so the GUI keeps working unchanged.
- `POST /api/searches` and `POST /api/filters/{id}/test` return **400** "This filter has no
  search terms, authors or institution — add at least one before running it." without
  queuing a job.
- The page shows that message; the Run button does not enter the Running state.
- `agents/monitor.py` skips an empty filter with a stderr line, same wording.
  (Category/date alone do not count: with no terms every source returns its whole date
  window. Say if you want category-only filters allowed.)

**C2 (D-a, D-b).** `SourceOrchestrator.search()` gets one optional parameter,
`enrich_only: Callable[[CanonicalRecord], bool] | None`. When given, only records it
accepts are enriched. Default `None` = today's behaviour (monitor, discover, GUI Discover
unchanged).
- Web search job keeps the matched **record objects**, passes
  `enrich_only=lambda r: id(r) in matched_ids`, and converts to dicts **after** the search
  returns, so results carry the enriched fields (PDF link, licence, filled abstract).
  Dedup merges into the existing object (`dedup.py:_merge` returns `existing`), so
  identity holds.
- GUI `SearchWorker` passes the same `enrich_only` so it stops enriching non-matches
  (its streamed rows stay as they are today — noted, not changed).
- **Flag:** this touches the orchestrator, which the 2026-09-15 plan (W1.a) said to wrap,
  not modify. It is required: enrichment is internal to `search()` and cannot be narrowed
  from outside.

**C3 (D-c, FR3).** `_enrich` reports through a new optional `on_enrich_progress(done,
total)`; if absent it falls back to `on_progress` (GUI bar unchanged). The job gains
`enriched` / `enrich_total` fields. The web job wires `on_enrich_progress` to those, so
`job.fetched` keeps the real fetched count.
- Progress line under the bar, updated every poll:
  `Found 150 · Matched 3 — Searching arXiv…` and during enrichment
  `Found 150 · Matched 3 · Enriched 2/3 — Enriching…`. Built by a pure function
  `progressText(job)` in `app.js`.
- The same line is used for a filter's **Test** run in the Filters tab.

**C4 (FR4).** Saved-filter Run buttons:
- On click: that button reads **Running…** and every saved-filter Run button plus the
  manual Search button is disabled before the request is sent.
- On finish: that button reads **Done** (or **Stopped** / **Failed** for a cancelled or
  errored run — "Done" on a failed run would be a false success). All buttons re-enabled.
- Clicking Run, Done, Stopped or Failed runs that filter again.
- Running/finished state is held in `state` and re-applied when the list is re-rendered
  (the list re-renders after a filter is saved or deleted).
- Label logic is a pure function `filterRunLabel(filterId, runState)`.

---

## 3. Acceptance criteria → tests

| ID | Criterion | Test |
|---|---|---|
| FR1.1 | Empty filter via `filter_id` → 400, no job queued, orchestrator not called | `tests/web/test_searches_routes.py::test_fr1_1_empty_saved_filter_is_refused` |
| FR1.2 | Empty inline `filter` → 400 | `tests/web/test_searches_routes.py::test_fr1_2_empty_inline_filter_is_refused` |
| FR1.3 | Filter Test route refuses an empty filter | `tests/web/test_filter_test_route.py::test_fr1_3_empty_filter_test_is_refused` |
| FR1.4 | Legacy `{"keywords": "x"}` group counts as a term (adversarial: looks empty to the old check) | `tests/test_filters_store.py::test_fr1_4_legacy_keywords_group_has_criteria` |
| FR1.5 | Whitespace-only fields / `authors: [""]` are empty | `tests/test_filters_store.py::test_fr1_5_blank_values_are_empty` (parametrised) |
| FR1.6 | monitor skips an empty filter and says so | `tests/test_monitor.py::test_fr1_6_empty_filter_is_skipped` |
| FR2.1 | With `enrich_only`, only accepted records reach Crossref/Unpaywall | `tests/test_orchestrator.py::test_fr2_1_enrich_only_limits_enrichment` |
| FR2.2 | Without `enrich_only`, all DOI records are enriched (existing behaviour) | existing `tests/test_orchestrator.py::test_crossref_enriches_records` |
| FR2.3 | Web results carry fields set by enrichment | `tests/web/test_searches_routes.py::test_fr2_3_results_include_enriched_fields` |
| FR2.4 | A search matching nothing makes zero enrichment calls | `tests/web/test_searches_routes.py::test_fr2_4_no_match_means_no_enrichment` |
| FR3.1 | `job.fetched` is not overwritten by enrichment progress | `tests/web/test_searches_routes.py::test_fr3_1_fetched_survives_enrichment` |
| FR3.2 | Poll payload has `matched`, `enriched`, `enrich_total` | `tests/web/test_jobs.py::test_fr3_2_poll_payload_has_counts` |
| FR3.3 | `progressText` renders Found/Matched, and Enriched only when enrichment started | `tests/web/test_frontend_wiring.py::test_fr3_3_progress_text` (node) |
| FR3.4 | `pollSearch` and `pollFilterTest` both use `progressText` | `tests/web/test_frontend_wiring.py::test_fr3_4_both_pollers_show_counts` |
| FR4.1 | `filterRunLabel` → Run / Running… / Done / Stopped / Failed | `tests/web/test_frontend_wiring.py::test_fr4_1_filter_run_label` (node) |
| FR4.2 | Run buttons disabled before the POST is sent; re-enabled on every finish path | `tests/web/test_frontend_wiring.py::test_fr4_2_buttons_disabled_before_request` (source-order check) |
| FR4.3 | Re-rendering the filter list keeps the Running/Done label | `tests/web/test_frontend_wiring.py::test_fr4_3_rerender_keeps_state` (node) |

Not code-testable: how the progress line and buttons look. Proposal: drive the real page in
the in-app browser against a local server after the build, run an empty filter, a filter
that matches nothing, and one that matches, and report what each shows.

## 4. Order

1. C1 + FR1 tests (highest impact: stops wasted runs).
2. C2 + C3 orchestrator/job changes + FR2/FR3.1–3.2 tests.
3. Front end C3/C4 + FR3.3–4.3 tests.
4. Full suite, browser check, learning-qa on the diff.

## 5. Adjacent issues found, not fixed

- GUI `SearchWorker` shows pre-enrichment rows (D-b applies there too). C2 stops the wasted
  calls but the GUI table still won't show enriched PDF links. Fixing it means changing how
  the GUI streams rows; left for a separate change.
- `web/routes_discover.py` enriches every fetched paper although Discover only reads titles
  and abstracts. Could pass `enrich_only=lambda r: False`.
