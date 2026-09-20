# QA gate — UI enhancements batch (learning-qa sweep)

**Date:** 2026-09-19
**Repo:** biorx
**Range:** `812d151...cd07d7e` (1 commit)
**Reviewer:** learning-qa failure-pattern sweep (generic P1–P36 + repo LEARNINGS P1–P12).
Cold pass run via a delegated leaf agent (`claude` CLI is not on PATH); fix commits re-swept
by a second cold pass.

## What the batch does

- **Sticky tab bar** — `.tab-bar { position: sticky; top: 0; z-index: 45 }` in
  `styles.css`; the notice is parked just under it at `top: 52px` (was `top: 8px`).
- **Select Filter dropdown** — the Search panel's Saved Filters list (a `<ul>` of
  per-filter Run buttons) becomes a single `<select id="search-filter-select">` with one
  `Run` button (`#btn-run-filter`). `loadSearchFilters()` populates the dropdown and
  preserves the selection across a rebuild; `runSelectedFilter()` runs the picked filter;
  `renderFilterRunButtons()` labels/disables the one Run button.
- **Ad Hoc Search** — the manual search card is renamed `Search` → `Ad Hoc Search`.

Files: `web/static/index.html`, `web/static/styles.css`, `web/static/app.js`,
`tests/web/test_frontend_wiring.py` (190 insertions, 82 deletions).

## RANGE

```
git diff 812d151...cd07d7e   (materialized at /tmp/sweep.diff, 466 lines)
BASE   812d15130db1a3e442ce514626be6e4a6b4a157d
HEAD   cd07d7ebf76664da8ee3ff035a59673e19ef8511  "feat(web): sticky tab bar, Select Filter dropdown, Ad Hoc Search"
```

Working tree is dirty but out of range: `filters.json` modified, `Maternal
nueroticism.pdf` deleted, untracked `.claude/`, `.test-qa-report.md`. None are part of the
reviewed range.

## TEST RESULTS

`venv/bin/python -m pytest tests/ -q` → **1095 passed, 1 skipped** (45s; 1094 + 1 before the
sweep fixes added a regression test). No `PytestReturnNotNoneWarning`, no `return`-in-test.

## APPLICABLE / CHECKED

Applicable: P4 (magic `52px` clearance), P9 (XSS sink), P12 (refactor-dropped side effect),
P19 (source-text assertions), P22 (stale refs after refactor), P25 (front-end wiring),
P27 (test that cannot fail), P29 (floor assertion); repo P10 (guard in one front end).

Checked:
- P1/P2/P5 — no new external calls; `loadSearchFilters` keeps its `try/catch → notice` and
  `/api/filters` is the only network call (unchanged).
- P9 — dropdown options and labels set via `textContent`/`value`, no `innerHTML`.
- P12 — list→dropdown conversion dropped per-filter button handlers, but disable/label/
  re-enable side effects were re-routed into `renderFilterRunButtons`, still called from
  `loadSearchFilters`, `startSearch`, and `searchFinished`; selection survival preserved.
- P22 — no remaining `search-filter-list` references anywhere in the repo.
- P19/P25/P29 — see findings; P25 and P29 were fixed (below).

NOT COVERED: logic/algorithmic correctness, concurrency/races, authn/authz, injection/security
beyond the XSS sink, performance, dependency/supply-chain, API-contract compatibility,
architecture. The sticky-tab behaviour and the `52px` clearance are layout properties the
headless suite cannot verify — a real-browser visual regression pass is still needed (unchanged
from prior batches).

## FINDINGS (pass 1, cold)

**1. HIGH · P25 — the dropdown + Run button wiring has zero test coverage.**
- Where: `web/static/app.js:2101-2102` (the `change` and `click` `addEventListener` lines).
- Risk: the node tests inject their own `onclick` and call `renderFilterRunButtons()`
  directly, so deleting either wiring line leaves the suite green while the Run button stops
  tracking the picked filter or stops running.
- Fix: added `test_e2_run_controls_are_wired` (source-grep, anchored, comment-stripped).

**2. HIGH · P29 — the notice-top assertion is a floor that cannot catch the drift it guards.**
- Where: `tests/web/test_frontend_wiring.py` (`test_e2_tab_bar_is_sticky`), `assert top > 0`.
- Risk: `> 0` stays green if the value regresses to `8px` and the notice slides behind the
  z-index-45 tab bar — exactly the defect the test's docstring claims to guard.
- Fix: pinned the exact value `== 52`, with a comment explaining it is a conscious-edit contract.

## FIX VERIFICATION

- `test_e2_run_controls_are_wired` — mutation-checked: deleted both `addEventListener` lines,
  ran the test → red (assertion on the first needle failed); restored the lines → green.
- `test_e2_tab_bar_is_sticky` — `== 52` matches `styles.css`'s `#notice { top: 52px }`
  verbatim (reviewer-verified), and is provable-failing on any drift or rule deletion.
- Full suite after fixes: **1095 passed, 1 skipped**.

## RE-SWEEP (fix commits, cold)

1 medium, 0 high:

**1. MED · P32 — the pinned `52` is sourced from the same `styles.css` the test reads.**
- The assertion proves the CSS didn't drift from itself, not that 52px actually clears the
  tab bar. If the tab bar's own padding/font grows its rendered height past 52px while
  `top: 52px` is left alone, the "notice behind the bar" defect reproduces and stays green.
- This is a residual limitation the fix tightens but does not create (the previous `> 0`
  floor was strictly weaker). A true fix needs a real-browser visual check, which this suite
  cannot run. Recorded as an open risk, not a blocker.

## VERDICT: APPROVED

Both pass-1 findings (P25, P29) are fixed and mutation-verified, and the full suite is green.
The re-sweep found one MEDIUM residual (a layout-coupling coverage limitation, not a defect
in the shipped behaviour) and nothing higher; it is a backlog item, not a blocker.
