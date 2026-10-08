# Implementation plan — show the date window, and an "All years" choice (2026-10-08, DW1–DW5, AY1–AY7)

**Request:** "plan both — date window in results and all years."
**Status:** APPROVED 2026-10-08; built. DW1–DW5, AY1–AY7 done (AY7 on a
local server; production needs the owner signed in).
**Changes from the plan:**
- The DW4 tests are in `tests/test_search_limits.py` and
  `tests/web/test_searches_routes.py` (`/api/config`), not
  `tests/web/test_app.py`.
- The AY4 route tests are in `test_searches_routes.py`, next to the FL1 ones.
- The heading and empty-note text moved into pure helpers
  (`resultsHeadingText`, `noMatchText`) so they could be tested.
- The date fields of both forms are listed in one table (`DATE_FIELD_IDS`),
  which the orphaned-controls test reads.
- AY7 live: 27, not 28. One Europe PMC record is a PsyArXiv preprint of a
  paper also in the list (published August 2026); de-duplication merges
  them, which is correct.
**Touches protected retrieval code:** none planned. Every source already takes
the start/end dates that `fixed_dates` (`src/filtering.py`, not protected)
writes out; All years only changes the start date written there.

---

## 0. Today

Owner's search: `internal family systems` in Title/Abstract.

| Where (live, 2026-10-08) | Papers |
|---|---|
| PubMed, all years | 24 |
| PubMed, 2016–2026 (`2016:2026[dp]`) | 18 |
| Europe PMC as PubMed (`SRC:MED`), the app's widest window (2016-10-10 to 2026-10-08) | 14 |
| Europe PMC (all of it), all years | 28 |

**Why the 4 papers between 18 and 14 are missing** (each PMID checked in Europe PMC):
- **3 papers have the phrase only in their author keywords** (34950063,
  35002818 — an erratum, 42325314). PubMed's `[tiab]` searches author
  keywords; Europe PMC's `TITLE_ABS` and the app's local filter (title and
  abstract only) do not. Not part of this plan; see §4.
- **1 paper is dated differently** (27500908): PubMed dates it by its print
  issue (2017 Jan); Europe PMC by when it first appeared online (2016-08-08).
  That is before the app's window starts (2016-10-10), so it falls outside.
- The app's window starts 2016-10-10, not 2016-01-01, because **Days back
  stops at 3,650**. Nothing tells the user which dates were searched.

The page never shows the window a search used. Its lines show only counts:
the progress line ("Found N · Matched M"), the results heading ("Results — N
matching"), the empty note, and the Filters-tab Test line. So a narrow window
looks like a lack of papers.

## 1. Behaviour

### DW — show the date window

- `fixed_dates` already fixes each search's start and end dates once (TD7). The
  job records them (`job.date_window = {"start", "end", "all_years"}`), and
  they reach the page in the job dict.
- A pure page function, `dateWindowText(window)`, returns:
  - `"Searched 2016-10-10 to 2026-10-08"`;
  - `"Searched all years (to 2026-10-08)"` when All years is ticked;
  - `""` when the job has no window (older jobs).
- The text is shown in four places:
  - the progress line: "Found 14 · Matched 12 · Searched 2016-10-10 to 2026-10-08 — …";
  - the results heading: "Results — 12 matching · Searched …";
  - the empty note: "No papers matched … between 2016-10-10 and 2026-10-08 — try …";
  - the Filters-tab Test line.
- A hint below the results, with its wording in `sources_config.yaml`
  (`date_window.hint`): "Dates are when a paper first appeared, online or in
  print, so a source's own date can differ." This covers the 27500908 case.
- The monitor's log line names the window too (it already logs the filter).

### AY — All years

- An **All years** checkbox beside Days back / Use date range, on the Search
  tab and in the Filters editor. Ticking it hides Days back and the date
  boxes. A saved filter stores it as `all_years: true`.
- `fixed_dates`: when `all_years` is true, `start_date` is
  `search.all_years_start` from `sources_config.yaml` (`"1900-01-01"`) and
  `end_date` is today. Any days_back or start_date stored in the filter is
  ignored.
- Live checks on 2026-10-08 show that every source takes this start date as an ordinary range:
  - Europe PMC: `FIRST_PDATE:[1900-01-01 TO …]` returned 28 for the owner's
    search;
  - arXiv: `submittedDate:[190001010000 TO …]` returned 26,434 for
    `cooperation`;
  - OSF: `date_created][gte]=1900-01-01` was accepted.
  - bioRxiv/medRxiv: the existing window rule already reads only the last 21
    days directly and shows its note. Older bioRxiv/medRxiv preprints come
    through Europe PMC.
- The search does not get wider anywhere else. PsyArXiv/SocArXiv still read
  by date when there is no Title word, so with All years they will hit the
  limit. The limit summary (WS) already says "put a key word in the Title
  box".
- The **Discover** tab is not changed. It keeps its own Days back.

## 2. Acceptance criteria and tests

### DW

| ID | Criterion | Test |
|---|---|---|
| DW1 | The job records the window that `fixed_dates` set: start, end, all_years. It is in `to_dict`. | `tests/web/test_searches_routes.py::test_dw1_window_on_the_job` (days_back 30 → start = today−30; a From/To filter → those dates) |
| DW2 | `dateWindowText` gives the three forms above. | `tests/web/test_frontend_wiring.py::test_dw2_date_window_text` (node-run) |
| DW3 | The progress line, results heading, empty note and Filters-tab Test line include the window, built with textContent. An older job with no window shows no text and no "undefined". | `test_dw3_*` (node-run `progressText`, `renderResults` with a fake DOM, `filterTestStatus`) |
| DW4 | The hint wording comes from `sources_config.yaml`. If the key is missing, a warning is logged and a built-in fallback is used. | `tests/web/test_app.py::test_dw4_config_hint`; `test_dw4_missing_key_falls_back` |
| DW5 | The monitor logs the window for each filter. | `tests/test_monitor.py::test_dw5_window_logged` |

### AY

| ID | Criterion | Test |
|---|---|---|
| AY1 | `fixed_dates` with `all_years: true` → start = `search.all_years_start`, end = today; days_back and stored dates are ignored. | `tests/test_filtering.py::test_ay1_all_years_dates` |
| AY2 | Adversarial (P7): `all_years: false`, `"false"`, `0`, missing, or `null` all keep the old window. Only a real `true` widens it. | `test_ay2_only_true_widens` |
| AY3 | Config: `search.all_years_start` is read from `sources_config.yaml`. If it is missing or not a date, a warning is logged and `1900-01-01` is used. The page and tests contain no other literal year. | `tests/test_search_limits.py::test_ay3_*` (alongside `search_limits`) |
| AY4 | Saved filters: create and update store `all_years` as a bool. A non-bool value is refused (400). The editor checkbox reads and writes it and hides the other date fields. | `tests/web/test_filters_routes.py::test_ay4_*`; node-run `buildFilterDict` / `selectFilter` test |
| AY5 | Search tab: `manualFilter()` sends `all_years: true` and no days_back or dates when the box is ticked. | node-run `test_ay5_manual_filter_all_years` |
| AY6 | Every source receives the wide window. A recording fake orchestrator sees start `1900-01-01` for every source. bioRxiv gets the RECENT note. | `tests/web/test_searches_routes.py::test_ay6_all_sources_get_the_window`; existing BW tests unchanged |
| AY7 | Live: the owner's search, All years, Europe PMC + PubMed, shows 28 Europe PMC records and the line "Searched all years (to …)". | local browser check (screenshot in status report); production check needs the owner signed in |
| R | Existing filters and searches behave as before. The full suite passes, and the drift test confirms no protected file changed. | full suite; `tests/web/test_no_retrieval_drift.py` |

## 3. Build order

AY3 config → AY1/AY2 (`fixed_dates`) → DW1 (job) → AY4 (filter routes) →
DW5 (monitor) → DW2/DW3/AY5 page + AY4 editor → DW4 hint → docs (CHANGELOG,
spec coverage) → full suite → AY7 local browser check → `/chdp` Hermes gate →
push → CI.

## 4. Not in this plan (owner decisions)

- **Author keywords** (3 of the 4 missing papers): PubMed's Title/Abstract
  search also matches author keywords; this app does not. Matching them
  would mean changing the Europe PMC query (protected: add `KW:` to the clause)
  and the local filter (add a keywords field to what is matched). It would
  also change what "Title or abstract" means across all sources. If wanted, it
  needs a separate plan.
- **Monitor with All years:** a scheduled filter with All years re-reads up
  to its limit from all years on each run. Papers already saved are not added
  again (the database prevents duplicates), but the run is slower. No guard
  is planned. A filter used by the monitor normally uses Days back.
- **The 3,650-day cap on Days back stays.** All years covers longer ranges.

## Adjacent issues found, not fixed

- The Filters-tab Test status line and the main search build their "matched"
  text separately (`app.js` around line 2115 and in `renderResults`). DW3 adds
  the window to both. Merging the two is out of scope.
