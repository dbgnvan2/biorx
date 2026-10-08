# QA Gate — duplicate-status RE-GATE (2026-10-07, DS1–DS4)

**RANGE:** `git diff origin/main..HEAD` (base `c23e1db`, 6 commits) — the full DS1–DS4
change plus the F1–F3 fix commit, re-swept as one range (fix commits are unreviewed code, P26).
**COMMITS:** e7972c9 (plan), 4ff7421 (fix, DS1–DS4), 146898c (docs), 81a5b2d (drift baseline),
29aca88 (fix, DS gate F1–F3), 7ac81b7 (drift baseline → DS gate fix)
**Plan:** `docs/implementation_plan_2026-10-07_duplicate_status.md`
**Prior gate:** `docs/cycles/2026-10-07_duplicate-status-qa-gate.md` (REJECTED: F1 shared
state, F2 own-repeats-as-earlier-source, F3 second bioRxiv line)
**Reviewer:** cold learning-qa failure-pattern sweep (delegate, read the three catalogues +
the diff, traced contracts through the modules without seeing how the code was written) +
independent re-verification of the three fixes below.
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1755 passed, 2 skipped** (exit 0)

## Verdict: APPROVED

All three findings from the prior gate are fixed and verified. The cold re-sweep of the
whole range (fix commit included) returned **no finding of MEDIUM or higher requiring a
fix** — the loop's stopping condition is met. Three LOW notes were returned and go to the
backlog, not the loop (P26: below-medium findings are not fixed in-loop; each fix is new
unreviewed surface).

---

## The three prior findings, verified fixed

### F1 — shared mutable state on the concurrent singleton — FIXED

The `self._last_duplicates` instance attribute is gone. `search()` now creates a
caller-owned `counts: Dict[str, int] = {}` **per source, inside the per-source loop**
(orchestrator.py:268), passes it to `_search_source` (orchestrator.py:282), and
`_search_source` writes `counts["already_found"]` / `counts["repeats"]` into it at
orchestrator.py:561-563. Nothing is stashed on the orchestrator, so two interleaved
searches cannot read each other's counts. Verified:

- `grep` of the module shows no `self._last_duplicates` and no `_last_duplicates`.
- The guard `test_ds2_counts_are_not_shared_between_concurrent_searches` asserts the
  instance has no attribute whose name contains "duplicate" or "already"
  (`vars(orch)`), which would have caught the old `_last_duplicates`.

### F2 — own repeats called "an earlier source" — FIXED

`_search_source` now separates the two causes of a duplicate. Before merging, it
snapshots the incoming record's own sources (`own = {h.source for h in
canonical.source_hits}`, orchestrator.py:505); after `dedup.add` merges, it checks
whether the merged record carries any source **not** in that snapshot
(orchestrator.py:516). A source not in `own` means an earlier source found the paper
first → `already_found`; otherwise it is this source's own repeat → counted as
`repeats = duplicates - already_found` (orchestrator.py:563). `fetched_status` renders
the two differently ("already found by an earlier source" vs "repeated within {label}").

Verified against the merge contract in `src/sources/dedup.py:146-151` (`_merge` appends
`source_hits` keyed on `(source, source_record_id)`; the `own` snapshot is taken from the
pre-merge record, so the merged set correctly reveals whether a new *source* appeared).
Adversarial test `test_ds2_own_repeats_are_not_an_earlier_source` pins
"PubMed: 10 new, 10 repeated within PubMed" and asserts no "earlier source" line.

### F3 — second status line for bioRxiv/medRxiv — FIXED

`search()` skips the `fetched_status` line when the adapter filters locally
(`not getattr(adapter, "filters_locally", False) is True`, orchestrator.py:288), matching
`_search_source`'s own `local_filter` test (orchestrator.py:414). `filters_locally` is a
`True` class attribute on `BiorxivMedrxivAdapter` (biorxiv_medrxiv.py:46) that survives
`for_search()`, so the real adapter is exempt and every other source still gets its line.
`test_ds3_local_filter_source_keeps_one_final_line` asserts exactly one final
bioRxiv/medRxiv line ("…papers read, 9 match the filter") and no second "fetched" line.

---

## Re-sweep of the fix commit (P26)

The fix commit 29aca88 is the least-reviewed code in the range and was swept as part of
the whole range. The cold review's own verdict: **PASS, no MEDIUM or higher**. Its three
LOW notes (recorded here, backlogged, not fixed in-loop):

1. **P19-corollary · tests/test_orchestrator.py** (the `inspect.getsource` guard) — a
   deletion guard that asserts on module source text is brittle: a reintroduction under a
   different name evades it, and a future comment naming the attribute would false-fail.
   The behavioural `vars(orch)` check is the primary guard. LOW.
2. **P6 · orchestrator.py:516** — a paper a source re-sends *after* a second source has
   already merged it is attributed to "an earlier source" though it is also that source's
   own repeat. Status-text only, near-unreachable in production. LOW.
3. **P19 · plan DS1 table** — the spec pins "four lines" but the F2 fix adds the
   "repeated within" variants; the code docstring and CHANGELOG already document them.
   Doc-drift only. LOW.

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| DS1 | `fetched_status` gives the four lines | `test_ds1_fetched_status`, green (plus the repeats variants) |
| DS2 | overlap named (full and partial), own repeats told apart | `test_ds2_overlap_is_named`, `test_ds2_own_repeats_are_not_an_earlier_source`, green |
| DS3 | records/counts/progress unchanged | full suite, green |
| DS4 | drift baseline advanced, guard sound | BASELINE=29aca88; protected diff empty; 4 drift tests green |
| F1 | no shared state on the singleton | `test_ds2_counts_are_not_shared_between_concurrent_searches`, green |
| F3 | one final bioRxiv line | `test_ds3_local_filter_source_keeps_one_final_line`, green |
| full suite | `venv/bin/python -m pytest tests/ -q` | 1755 passed, 2 skipped |

## Notes

- The change remains **status text only**: nothing fetched, merged, kept or counted
  changed (DS3 green; the only new runtime state is the per-source local `counts` dict,
  which is not shared).
- The reviewer's `NOT COVERED` is explicit: concurrency (race audit), logic correctness,
  security, test-quality, and architecture families were not systematically assessed —
  F1's shared-state class was verified by code-path reading, not a race audit. This is
  the learning-qa scope limit, not a gap introduced by this change.
- The three LOW notes belong in the backlog (LEARNINGS.md "Open risks"), not in this
  change; the gate's stopping condition (no MEDIUM-or-higher) is satisfied.
