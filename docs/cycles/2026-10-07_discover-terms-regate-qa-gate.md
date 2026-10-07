# QA Gate — Discover Terms find papers, RE-GATE (2026-10-07)

**Range:** `git diff origin/main...HEAD`
**Commits:** 37554b8, b383efb, 2b5b2ce, 01325e6
**Plan:** `docs/implementation_plan_2026-10-07_discover_terms.md` (DT6–DT8)
**Reviewer:** learning-qa (failure-pattern sweep); warm re-sweep of the fix commit
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1634 passed, 2 skipped** (green)

## Verdict: APPROVED

The prior gate (`2026-10-07_discover-terms-qa-gate.md`) was REJECTED on three
findings. Commit `01325e6` addresses all three; each is verified below, the
HIGH finding is mutation-proven, and no new finding of medium or higher severity
survives the re-sweep of the whole range.

---

## Fix verification (the three prior findings)

### 1 · P25/P19 · HIGH · `web/static/app.js:2008` — FIXED

The crux line `state.discoverDays = Number(job.result.days_back) || null` was
never exercised (DT6.B stubbed `state.discoverDays` directly).

New test `tests/web/test_frontend_wiring.py::test_dt6a_poll_carries_days_back_into_a_term_filter`
drives the **real** `pollDiscover` (extracted from `app.js`) with a stubbed
`api()` returning `{status: "done", result: {…, days_back: 90}}`, then the real
`createFilterFromTerm`, and asserts the filter saves with 90 days; a second
case (result without `days_back`) asserts 7.

**Mutation-proven (P27):** deleting the crux line and re-running the test goes
red — `assert 7 == 90` (`test_frontend_wiring.py:3263`). Restored after the
probe; `git status` clean. The producer (`result.days_back`) and the consumer
(`state.discoverDays`) contract is now exercised end to end.

### 2 · P19/P32 · MED · `src/discover.py` wildcard copy — FIXED

`wildcard_pattern(term)` is now extracted into `src/filtering.py:141` and
returns `re.compile(r"(?<!\w)" + re.escape(term[:-1]))` — byte-for-byte the
pattern `match_term` previously built inline. `match_term` (line 162) now calls
it, and `src/discover.py::_whole_word_pattern` (line 120) calls it for the `*`
branch. Single source of truth via import, not a third hand-maintained copy.

`tests/web/test_discover_routes.py::test_dt8a_wildcard_counts_agree_with_the_filter`
asserts `count_term_hits([term], papers)[term] == sum(match_term(term, …))` over
a battery of `*` terms (adolescen*, adult*, teen*, sleep* — including the
`preadolescent` / `adultery` edge cases that pin the word-boundary semantics).

The `match_term` refactor is behaviour-preserving (identical pattern), and
`src/filtering.py` is **not** in `PROTECTED`, so `test_no_retrieval_drift.py`
(4 passed, baseline not advanced) is unaffected. DT-R2 holds.

### 3 · P19-corr/P27 · MED · source-text assertions — FIXED

Both greps are gone; no `body.count("${note}")` or `"papers_sampled === 0" in
body` remains in the range. Replaced with behavioural node-runs:

- `test_dt6c_add_term_puts_the_note_in_its_message` — runs the real
  `addTermAsGroup` with a stubbed `saveTermFilter` that captures the messages,
  and asserts the window note is present in both the appended-name and
  kept-name messages at 7 days and absent at 90 days (also covers the
  name-too-long path).
- `test_dt8b_page_says_found_but_untitled_not_none_found` — runs the real
  `pollDiscover` on a found-but-untitled job and asserts the *rendered*
  `discover-terms-chips` text says "3 papers found … none had a title" and not
  "No papers found".

Both tests would go red on a dead branch or a benign reword of the wrong
surface.

---

## Re-sweep of the whole range (including the fix commit, per P26 corollary)

Traced the fix-commit code and the full chain end to end
(`_run_discover` → result shape → `pollDiscover` → `renderDiscoverChips` →
`createFilterFromTerm`/`addTermAsGroup`). No finding of medium or higher severity.

Below-threshold observations (no action required for this gate):

- `docs/implementation_plan_2026-10-07_discover_terms.md` §0 still says
  "`src/filtering.py` is read, not changed"; the finding-2 fix made that
  sentence stale. The change is a behaviour-preserving refactor outside the
  retrieval layer — a documentation nit, not a defect.
- `chipCount` computes `total` as `papers_sampled ?? papers_found`. `papers_sampled`
  is 0 only on the early-return path where `terms` is `[]`, so `renderDiscoverChips`
  never reaches `chipCount` with a 0 total — no "0 of 0" is renderable.
- `state.discoverDays` is set fresh in the `done` branch immediately before
  `renderDiscoverChips`, so a no-papers/untitled run cannot leak a stale window
  into a later chip click (no chips are drawn on those branches).
- Case-insensitivity of the hit count is real: `split_terms` lowercases terms
  and `count_term_hits` lowercases the text (verified by direct probe;
  `test_dt8a_term_hits_counted_against_full_abstract`'s "OLDER ADULTS" case
  passes for the right reason).
- The deliberate whole-word vs substring divergence of `count_term_hits`
  relative to `match_term` is disclosed in the docstring and the plan, and is
  not itself a finding (unchanged from the prior gate).

---

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P19 (and its
source-text corollary), P21, P25, P26, P27, P29, P32. Matcher semantics
verified against `src/filtering.py` (`match_term`, `split_terms`,
`wildcard_pattern`).

Not covered (unchanged from the prior gate; not re-run): logic/algorithmic
correctness, concurrency/races, authn/authz, injection/security, performance,
dependency/supply-chain, API-contract compatibility, broad test-quality,
architecture. Live model/source behaviour (DT-R4) is human-only and remains
pending after deploy.

---

## Notes

- This re-gate is a warm re-sweep (one bounded pass over the fix commit plus
  the whole range), per P26's cap on warm passes. The prior gate already
  contributed one cold pass; the mutation proof on the HIGH finding is the
  deciding evidence here, not a falling finding count.
- Suite went from 1632 to 1634 passed: `01325e6` adds exactly two net tests
  (the wildcard-agreement test and the poll-carries test), both of which
  execute (node is present) and pass.
