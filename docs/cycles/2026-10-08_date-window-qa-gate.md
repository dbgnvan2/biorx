# QA Gate — show the date window; an "All years" choice (2026-10-08, DW1–DW5, AY1–AY7)

**RANGE:** `git diff origin/main...HEAD` (commits `2335094` plan, `c32c5df` feature),
plus one gate-driven fix in the working tree (`src/filtering.py`,
`tests/test_filtering.py` — see M1).
**Plan:** `docs/implementation_plan_2026-10-08_date_window.md`
**Reviewer:** learning-qa failure-pattern sweep, cold and self-contained — read
the materialized diff plus the three playbooks, traced the
filter → `fixed_dates` → query builders/orchestrator → job dict → JS helpers
contract end to end, and did not see how the code was written.
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1870 passed, 2 skipped**
(green; the +1 over the pre-fix run of 1869 is the M1 regression test).

## Verdict: APPROVED

The feature meets every acceptance criterion, the full suite is green, no
protected retrieval file changed, and the one MEDIUM finding (M1) was fixed and
re-swept clean. The two remaining findings are LOW and accepted as by-design /
matching existing convention; they go to the backlog, not the loop.

---

## APPLICABLE

P2 (silent drop — a malformed value narrowing with no signal), P4 (hardcoded
year/threshold in logic vs config), P5 (inconsistent hardening across sibling
entry points — search route vs saved-filter route vs monitor), P7 (adversarial —
only a real `true` may widen the window), P13 (a stage narrowing without saying
so), P19 (producer/consumer drift — the `{start,end,all_years}` window contract
Python ↔ JS ↔ tests), P25 / repo-P10 (a guard that lives in one front end), and
repo-P9 (XSS — no `innerHTML` for server-supplied text).

## CHECKED

### 1. `fixed_dates` widens only on a real `true` (P7) — PASS

The predicate is a single source of truth: `filter_dict.get("all_years") is True`
in `fixed_dates` (src/filtering.py:235), and `is True` again in `date_window`
(src/filtering.py:256). `test_ay2_only_true_widens` parametrizes `False`,
`"false"`, `"true"`, `0`, `1`, `None`, `"yes"`, plus the key-missing case, and
asserts the window stays at `days_back` for every one. Non-booleans can never
widen — they fall through to `get_date_range`.

### 2. All years reaches every source via the real orchestrator — PASS

`test_ay6_all_sources_get_the_window` builds the real `SourceOrchestrator` and
replaces only the network adapters with recorders, so the real `_build_query`
and routing run. It covers all six search adapters the orchestrator registers
(`europepmc`, `pubmed`, `arxiv`, `psyarxiv`, `socarxiv`, `biorxiv_medrxiv`) and
asserts each sees the wide window: `FIRST_PDATE:[1900-01-01 TO` for Europe PMC
and PubMed, `submittedDate:[190001010000 TO` for arXiv, and `start_date ==
"1900-01-01"` in the `filter_dict` for the date-range sources, plus the
bioRxiv/medRxiv "last 21 days" note. (OSF is not a separate adapter — PsyArXiv
and SocArXiv are the OSF preprint servers, so the six names are the complete
search set.)

### 3. Saved-filter validation — PASS

`_check_all_years` (web/routes_filters.py:80) is applied in both `create_filter`
and `update_filter`: a non-bool `all_years` is refused (400), and `false` is
dropped so only `true` is ever stored. `test_ay4_all_years_must_be_a_bool`
(parametrized `"true"`, `1`, `0`, `None`, `"yes"`) and
`test_ay4_all_years_saved_and_read_back` pin both directions, and the filter
editor's read/write is covered by the node-run `test_ay4_editor_reads_and_writes_all_years`.

### 4. The page builds text with `textContent` only (repo-P9) — PASS

No `innerHTML` is introduced. The four display sites — progress line, results
heading, empty note, Filters-tab Test line — are pure string builders
(`progressText`, `resultsHeadingText`, `noMatchText`, `searchedSuffix`) whose
results are assigned via `.textContent`, and the new `date-window-hint` element
is written with `.textContent` from `state.dateWindowHint` (sourced from the
`/api/config` endpoint). The XSS test in the suite stays green.

### 5. Config fallbacks (P4) — PASS

`all_years_start` (src/search_limits.py:50) and `date_window_hint`
(src/search_limits.py:71) read from `sources_config.yaml`
(`search.all_years_start`, `date_window.hint`); a missing/malformed value logs a
WARNING and returns a built-in fallback. `test_ay3_*` (7 cases) and `test_dw4_*`
(5 cases) cover the fallbacks. No literal year is baked into the page or query
builders — `1900-01-01` lives only in config and the fallback constant.

### 6. Monitor path — PASS

`run_search` (agents/monitor.py:146) calls
`fixed_dates(filter_dict, getattr(orchestrator, "config", None))`, and
`SourceOrchestrator.__init__` stores `self.config` (orchestrator.py:87), so the
monitor reads the same configured start date as the web route. DW5 logs the
window for both the normal and All-years cases; `test_dw5_window_logged`
asserts the exact lines.

### 7. No protected retrieval file changed — PASS

`git diff origin/main...HEAD --name-only` contains none of the protected
retrieval files (`tests/web/test_no_retrieval_drift.py` unchanged); the drift
guard passes in the full suite.

## FINDINGS

### M1 · P5/P10 · MEDIUM (fixed) · `all_years` validation lives in one front end

The saved-filter routes refuse a non-bool `all_years` (400), but the ad-hoc
search route and `agents/monitor.py::run_search` accepted one silently — a
direct API client or hand-edited `filters.json` sending `"true"`/`1` got a
narrower `days_back` search with no 400 and no warning. `fixed_dates`/`date_window`
never *widened* on it (`is True`), so the failure was silence, not over-widening
— but a silent narrowing is exactly the confusion this feature exists to remove
(the plan's §0: "a narrow window looks like a lack of papers").

**Fix:** in `fixed_dates` (the shared run path both the search route and the
monitor call), a present-but-non-bool `all_years` is now dropped with a WARNING,
so no run path narrows silently. The saved-filter route keeps its stricter 400.
Regression test `test_ay2_non_bool_all_years_warns_not_silent` asserts the value
does not widen, is dropped from the dict, and logs. Re-swept clean; full suite
green after the fix.

### L1 · P13 · LOW (accepted — by design) · web/static/app.js `dateWindowText`

The All-years form is `"Searched all years (to END)"`, omitting the start date.
This is the exact wording the plan specifies (DW2); "all years" already implies
"from the beginning" at the shipped `1900-01-01`, and the monitor log names the
full range. Backlog: if `all_years_start` is ever configured to a non-trivial
date, surface it in the page text or hint.

### L2 · P4 · LOW (accepted — matches existing convention) · src/search_limits.py

`_ALL_YEARS_FALLBACK` and `_HINT_FALLBACK` duplicate the values in
`sources_config.yaml`, and `test_ay3_start_from_config` asserts the literal
against the real YAML. The fallback fires only when config is missing/malformed,
and the pattern matches the existing `_FALLBACK = (200, 2000)` in the same
module. Backlog: derive the fallback from config or document it as a frozen
default.

## NOT COVERED

- AY7's live end-to-end browser check (the owner's search, All years) is
  recorded in the plan/spec as done on a local server, but was not re-run from
  this machine — no live network calls in the gate.
- The two skipped tests are environmental and out of scope:
  `test_fulltext.py::…` (RLIMIT_AS is Linux-only) and
  `test_h_environment.py::test_h_runs_on_the_supported_python` (CI-only).
- Outside the failure-pattern family, the reviewer did not assess: logic/
  algorithmic correctness, concurrency/races, authn/authz, injection and other
  security classes (beyond the P9 textContent check), performance, dependency/
  supply-chain, or API contract compatibility.

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| DW1 | job records the window (start, end, all_years) in `to_dict` | `test_dw1_window_on_the_job` (3 shapes), green |
| DW2 | `dateWindowText` gives the three forms; "" for older jobs | `test_dw2_date_window_text`, green |
| DW3 | four display lines carry the window, built with textContent; no "undefined" | `test_dw3_*` (4), green |
| DW4 | hint wording from config; fallback + warning when missing | `test_dw4_*` (5 + route + node-run), green |
| DW5 | monitor logs the window per filter | `test_dw5_window_logged`, green |
| AY1 | `all_years:true` → configured start, end today; days_back/dates ignored | `test_ay1_all_years_dates`, green |
| AY2 | only a real `true` widens; non-bool warns, not silent | `test_ay2_only_true_widens` + `test_ay2_non_bool_all_years_warns_not_silent`, green |
| AY3 | start date from config; fallback + warning when missing/bad | `test_ay3_*` (7), green |
| AY4 | saved filters store bool; non-bool refused 400; editor reads/writes and hides date fields | `test_ay4_*` (6 + node-run), green |
| AY5 | Search tab sends `all_years:true`, no days_back/dates | `test_ay5_manual_filter_all_years`, green |
| AY6 | every source receives the wide window (real orchestrator) | `test_ay6_all_sources_get_the_window`, green |
| R | existing behaviour unchanged; full suite; no protected file changed | 1870 passed, 2 skipped; drift guard green |

## Notes

- This gate introduced one fix commit (M1), so — unlike the 2026-10-07 gate —
  there is new unreviewed code to account for. The fix was re-swept as its own
  range and is clean: it mutates only `fixed_dates`' internal `normalise_filter`
  copy (no caller dict), handles `None` as absent and `False` as present, and
  the `is True` predicate remains the single source of truth.
- The stopping condition is met: no finding of MEDIUM or higher remains
  unfixed; the two LOW findings go to the backlog.
