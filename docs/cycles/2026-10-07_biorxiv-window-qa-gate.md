# QA Gate — bioRxiv/medRxiv direct read only where it helps (2026-10-07, BW1–BW7)

**RANGE:** `git diff origin/main...HEAD` (base `16fc2f9`)
**COMMITS:** 46eea0c (plan), 203668f (fix, BW1–BW7), c17ae79 (drift baseline)
**Plan:** `docs/implementation_plan_2026-10-07_biorxiv_window.md`
**Reviewer:** learning-qa failure-pattern sweep (cold, self-contained — read the
diff and traced the contracts through the modules; did not see how the code was
written)
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1750 passed, 2 skipped** (green)

## Verdict: APPROVED

Every gate check passes, the full suite is green, and no finding rises above
LOW. The rule is a single source of truth shared by the adapter and the job
note, a skipped range makes zero requests and is reported as neither a failure
nor a page-limit hit, short recent ranges round-trip byte-identical dates, the
date boundaries are exact, and the config defaults match the plan. Two LOW
observations are recorded for the backlog; neither is introduced by this change.

---

## APPLICABLE

P2 (silent drop — the old 2019–2026 range read 150 pages from January 2019 and
silently stopped), P5 (sibling calls — the web route and agents/monitor.py must
show the same rule the adapter follows), P9 (real-scale — the reported 589,593
paper range), P19 (producer/consumer drift — the note must not hand-copy the
read decision), P26 (fix commit — the drift-baseline advance is itself a
retrieval edit that must be re-swept), P28 (test→real-artifact isolation — the
drift-baseline guard), P29 (exact assertions — boundaries pinned with `==`, not
floors).

## CHECKED

### 1. The adapter and the job note share one rule — PASS

Both paths call the same `biorxiv_direct_window` (src/sources/biorxiv_window.py:36),
with the same inputs derived the same way:

- Adapter (src/sources/biorxiv_medrxiv.py:99): `get_date_range(fd)` →
  `biorxiv_direct_window(start, end, _today(), self._max_direct_days,
  self._lag_days)`. It reads only `window.mode` / `window.start` / `window.end`
  and discards `window.note`.
- Note (src/sources/biorxiv_window.py:71 `biorxiv_notes`): `get_date_range(fd)`
  → `biorxiv_direct_window(start, end, today, max_days, lag_days,
  europepmc_selected=...)`, then returns `window.note`.

`max_days` / `lag_days` come from the same `window_settings(sources_config)` in
both. The `europepmc_selected` flag affects only the note's "Tick Europe PMC"
tail, never the mode/start/end decision, so the read and the note cannot disagree
on *what* is read (P19, pitfall 2 — no parallel hand-copy of the rule; the rule
lives once in `biorxiv_direct_window`).

### 2. A skipped range sends no request and is not a failure — PASS

Adapter skip path (biorxiv_medrxiv.py:101-105) returns `[]` before any request
and sets `has_more = False`, `last_page_size = 0`, `last_total = 0`.
`_search_source` (orchestrator.py:441) breaks on `page_size_seen == 0` with
`fetched = 0`, and `limited` / `limited_by_pages` stay False, so the post-loop
"page limit reached" status (orchestrator.py:548-556) and `_report_failure` are
never reached. Proven by `test_bw3_old_range_makes_no_requests` (calls `== []`,
`has_more is False`) and `test_bw3_skip_is_not_a_failure` (failures `== []`, no
"page limit" status) — the latter is the real-scale P9 case (2019–2020, formerly
300 requests).

### 3. Short recent ranges behave exactly as before — PASS

In FULL mode `window.start/end` are `s.isoformat()` / `e.isoformat()`.
`get_date_range` (query_builder.py:261-263) already returns `%Y-%m-%d` strings,
so `date.fromisoformat(s[:10])` then `.isoformat()` is a byte-identical
round-trip. The existing B7 tests pass; `test_b7_date_range_used` was the only
one edited, and only because its old 2020–2026-06 range is now a skip (the plan's
"Changes from the plan" records this). No other B7 behaviour changed.

### 4. Date boundaries are exact — PASS

`biorxiv_direct_window` semantics and the BW1 table agree:

- `e < today - lag_days` → skip; so `e == today - lag_days` is **not** skipped.
  Test: end 2026-08-08 (exactly 60 before 2026-10-07) → FULL; end 2026-08-07 → SKIP.
- `(e - s).days + 1 > max_days` → recent; so a range of exactly `max_days` is FULL.
  Test: 2026-09-17→10-07 (21 days) → FULL; 2026-09-16→10-07 (22) → RECENT.
- RECENT start = `e - (max_days - 1)`, so exactly `max_days` days are read.
  Test: clipped start 2026-09-17 for max_days 21.

All cases assert exact `mode/start/end` tuples (`==`, P29), not floors.

### 5. Config defaults — PASS

`window_settings` (biorxiv_window.py:59) reads `publication_sources.
biorxiv_medrxiv` and returns `(21, 60)` with a WARNING when either key is
missing. `sources_config.yaml` carries `max_direct_days: 21` and
`europepmc_lag_days: 60`; `test_bw7_repo_config_has_the_keys` reads the real
file and asserts both are positive. The drift-baseline advance
`b384965 → 203668f` (test_no_retrieval_drift.py:128-131) is a documented W1.a
exception; the guard still holds at HEAD (empty protected diff) and
`test_the_guard_would_notice_a_change` still passes, so the advance did not
blind the guard (P27).

## NOT COVERED

- BW-L live on production (needs a signed-in browser; recorded in the plan and
  spec_coverage_webapp.md).
- `_servers()` reading `biorxiv_medrxiv.servers` from the config's top level
  rather than `publication_sources` — flagged "Adjacent, not fixed" in the plan
  and TODO.md; not in this batch's scope.

## FINDINGS

### L1 · LOW (observation) · src/sources/biorxiv_window.py:71 vs biorxiv_medrxiv.py:33

The note is computed with `date.today()` at job start (routes_searches.py:158,
agents/monitor.py) while the adapter evaluates `_today()` when the search reaches
bioRxiv/medRxiv, potentially minutes later. A search queued across midnight
could show a note that disagrees with the actual read at the lag boundary.
Cosmetic-only and astronomically rare; the mode decision still comes from the
same function. Not blocking.

### L2 · LOW (non-blocking) · web/routes_searches.py:157, agents/monitor.py:144

Both call `orchestrator._resolve_active_sources(...)`, a private method, to know
which sources are active. Functionally correct and plan-specified, but a public
accessor would stop the web/agent layer coupling to a private name. Not blocking.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| BW1 | full / recent / skip rule, exact boundaries | `test_bw1_window` (7 cases) + `test_bw1_notes_*`, green |
| BW2 | long recent range reads only the newest `max_days` | `test_bw2_long_range_reads_only_the_newest_days`, green |
| BW3 | old range: no requests, no failure, no page-limit status | `test_bw3_old_range_makes_no_requests`, `test_bw3_skip_is_not_a_failure`, green |
| BW4 | short range unchanged | `test_b7_*` (one moved to a recent range, documented), green |
| BW5 | note on the job / monitor log, same rule | `test_bw5_notes`, `test_bw5_*` (4 web tests), green |
| BW6 | page shows notes via textContent | `test_bw6_job_notes_shown`, `test_bw6_poll_passes_the_notes`, green |
| BW7 | config keys; missing keys warn and use 21/60 | `test_bw7_settings_from_config`, `test_bw7_repo_config_has_the_keys`, green |
| drift | baseline advanced, guard still sound | 4 drift tests pass, empty protected diff at HEAD |
| BW-L | live on production | not done (needs signed-in browser) |
| full suite | `venv/bin/python -m pytest tests/ -q` | 1750 passed, 2 skipped |

---

## Notes

- One cold review pass ran. It independently confirmed the single-source routing
  (adapter and `biorxiv_notes` both call `biorxiv_direct_window`), the exact
  boundary semantics, the skip path's absence of any failure emission, and the
  byte-identical date round-trip in FULL mode.
- No fix commit was introduced. The loop's stopping condition — no finding of
  MEDIUM or higher requiring a fix — is met; the two LOW findings go to the
  backlog.
