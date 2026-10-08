# QA Gate — duplicate-status (2026-10-07, DS1–DS4)

**RANGE:** `git diff origin/main...HEAD` (base `c23e1db`)
**COMMITS:** e7972c9 (plan), 4ff7421 (fix, DS1–DS4), 146898c (docs), 81a5b2d (drift baseline)
**Plan:** `docs/implementation_plan_2026-10-07_duplicate_status.md`
**Reviewer:** learning-qa failure-pattern sweep (cold, self-contained — read the
diff and traced the contracts through the modules; did not see how the code was
written) + a second independent verification of the three named criteria below.
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1752 passed, 2 skipped** (green)

## Verdict: REJECTED

The three criteria named for this gate all pass, and the full suite is green.
But the cold review returned **two MEDIUM findings**, so the loop's stopping
condition — *no finding of MEDIUM or higher requiring a fix* — is not met.

- The change is **status text only**: nothing fetched, merged, kept or counted
  changes (verified below; DS3 green).
- The duplicate count **cannot leak from one source to the next** within a
  search (the `= 0` reset + end-of-source set handle the sequential case).
- The **lines are exact** against the DS1 table.

The blocker is **finding 1**: the new `self._last_duplicates` is shared mutable
state on a `SourceOrchestrator` the codebase documents as a process-wide
singleton serving every job thread, and which `test_b6_concurrent_searches_do_not_share_cursor`
proves runs searches concurrently. This reintroduces the exact class B6 fixed,
and the fix is mechanical (return the count instead of stashing it on `self`).
Findings 2 and 3 are lower-priority wording/redundancy issues in the same line.

---

## APPLICABLE

P6 (a status line asserting a cause — "earlier source" — without proving it),
P5 (sibling/class consistency — the B6 per-search-isolation precedent), P8
(dirty/shared state across runs — the shared attribute on a singleton), P26
(fix commit — the drift-baseline advance is itself a retrieval edit that must
be re-swept). Repo P12 (one channel, two quantities) is marginal and was checked.
Highest-risk: P6/P5 — the shared `_last_duplicates` attribute whose value the
status line attributes a cause to without proving it.

## CHECKED

### 1. Status text only — PASS (the named criterion holds)

The only behavioural change is the string emitted at orchestrator.py:277. The
new `_last_duplicates` attribute is written at orchestrator.py:401/539 and read
only at orchestrator.py:277 to feed `fetched_status(...)`. Every count the
retrieval path uses is untouched:

- `fetched` (new records), `duplicates` (merged records), `dedup` growth, and
  the local `seen_raw` / `src_total` / `not_matching` / `unreadable` accounting
  are byte-for-byte the same as at `origin/main`.
- `_search_source` still returns `fetched` (an int); `total_fetched`,
  `known_total`, `dedup.results()` and `_rank(records)` are unchanged.
- `_enrich` is not reached differently; the bioRxiv local-filter accounting
  (orchestrator.py:540-557) is unchanged.

DS3 (records/counts/progress unchanged) is corroborated by the unmodified
orchestrator and route tests passing. `_last_duplicates` *adds* a value but
never alters what is fetched, merged, kept or counted.

### 2. No leak from one source to the next — PASS (sequential)

`_search_source` sets `self._last_duplicates = 0` at entry (orchestrator.py:401)
and `self._last_duplicates = duplicates` after the pagination loop
(orchestrator.py:539). Every non-exception path reaches 539 before `search()`
reads it at 277; the two exception paths that skip 539 (`SourceUnavailableError`
with `fetched == 0` → `raise`; the generic `except Exception` → `continue`) both
`continue` in `search()` without reading the attribute. So a source's own
duplicate count cannot be carried into the next source's line.

### 3. Lines are exact — PASS

`fetched_status` (orchestrator.py:51-55) matches the DS1 table verbatim,
including the comma grouping:

| new | duplicates | emitted | spec |
|---|---|---|---|
| N | 0 | `{label}: N fetched` | ✓ unchanged |
| 0 | D | `{label}: D papers read, all already found by an earlier source` | ✓ |
| N | D | `{label}: N new, D already found by an earlier source` | ✓ |
| 0 | 0 | `{label}: 0 fetched` | ✓ unchanged |

`test_ds1_fetched_status` pins all four plus `1,234 new, 5,678 already found`;
`test_ds2_overlap_is_named` pins the real `Europe PMC` / `PubMed` labels and the
full-overlap and partial-overlap lines.

### 4. Full suite — PASS

`venv/bin/python -m pytest tests/ -q` → **1752 passed, 2 skipped** (exit 0). The
drift-baseline advance (c23e1db-adjacent `203668f → 4ff7421` in
`tests/web/test_no_retrieval_drift.py:128-131`) is a documented W1.a exception;
the guard still holds at HEAD (the protected diff is empty — only the BASELINE
line changed), so the advance did not blind the guard.

## NOT COVERED

Per the learning-qa scope limits: logic/algorithmic correctness, concurrency/races
(finding 1 is concurrency-*adjacent* and is flagged as such, not claimed as a
race audit), authn/authz, injection/security, performance, dependency/supply-chain
risk, API-contract compatibility, test quality, architecture. DS-L live on
production is not in this batch.

## FINDINGS

### F1 · MEDIUM · src/sources/orchestrator.py:277 vs :401/:539, :300-307

**Shared mutable status state on a documented singleton reintroduces the B6
class.** `self._last_duplicates` is a plain instance attribute on a
`SourceOrchestrator` that `web/deps.py:64-66` builds once per process and the
docstring (orchestrator.py:300-307) describes as "one orchestrator serves every
job thread". `src/jobs.py` runs the search lane with 2 workers, and different
users (or a discover job on the model lane, `web/routes_discover.py:91`) can run
`search()` concurrently. B6 (`_adapter_for_search` handing out a fresh adapter,
per-`search()` `dedup`) isolated per-search state precisely because of this; the
new attribute smuggles a per-source result back through shared state instead of a
return value. Two interleaved searches can read each other's duplicate count into
the wrong source's line. The `= 0` reset at 401 does not close it: it runs at
`_search_source` entry, not at the read, and it only protects the *sequential*
case (where it is redundant — 539 always executes before 277 on every
non-exception path).

**Fix:** return the count — `return fetched, duplicates` from `_search_source` —
and read the local at 277; drop the attribute. This touches the one production
call site (orchestrator.py:261) and the `_search_source` call sites in
`tests/test_orchestrator.py:438,460,484,521`, `tests/test_batch_h.py:158`,
`tests/test_batch_i.py:503,536,547,590,625` (mechanical `fetched` →
`fetched, _`).

**Confidence:** high that the shared-state defect exists and violates the repo's
own invariant; medium that it manifests in production (needs concurrent runs).

### F2 · MEDIUM · src/sources/orchestrator.py:54-55 vs :489-494, :546

**The line asserts a cause the count does not verify.** `duplicates` is "matched,
but a paper already read" — any record already in the deduplicator, whether
added by a *prior source* or an *earlier page of the same source* (e.g. an index
shifting mid-pagination). The wording "already found by an earlier source"
attributes it to a cross-source cause. The bioRxiv/medRxiv path makes this
concrete: its own log line (orchestrator.py:546) honestly says "repeat a paper
already read", while `fetched_status` would label the same number "already found
by an earlier source" for the same source. For the motivating PubMed-after-Europe-PMC
case the wording is correct; for same-source repeats it is not.

**Fix:** either distinguish cross-source duplicates from same-source repeats
(track which source/iteration added each record), or soften to "already found" /
"already read" to match the honest bioRxiv phrasing.

**Confidence:** medium (semantic mismatch is verifiable; production frequency
depends on adapter pagination).

### F3 · LOW · src/sources/orchestrator.py:277 vs :553-556

**Local-filter sources get a second, now-possibly-wrong status line.** For
`biorxiv_medrxiv` (and any `filters_locally` adapter), `_search_source` already
emits its own "N papers read, M match the filter (…repeats…)" line
(orchestrator.py:556-557), then `search()` unconditionally emits `fetched_status`
at 277. When `duplicates == 0` the second line is a redundant "N fetched"; when
`duplicates > 0` it repeats F2's wrong "earlier source" claim against a source
that reads only a date window. The plan (DS1, line 32) says bioRxiv "keeps its
own fuller line", but the code does not exempt it.

**Fix:** skip the `fetched_status` line for `local_filter` sources, or fold the
counts into the single bioRxiv line.

**Confidence:** high that the double line occurs (deterministic); low severity
(cosmetic).

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| DS1 | `fetched_status` gives the four lines | `test_ds1_fetched_status`, green |
| DS2 | overlap named (full and partial) | `test_ds2_overlap_is_named`, green |
| DS3 | records/counts/progress unchanged | full suite, green |
| DS4 | drift baseline advanced, guard sound | 4 drift tests pass, empty protected diff at HEAD |
| full suite | `venv/bin/python -m pytest tests/ -q` | 1752 passed, 2 skipped |

---

## Notes

- One cold review pass ran, plus an independent re-verification of the three
  named criteria (status-text-only, sequential no-leak, exact lines). All three
  hold.
- The three named criteria are NOT the blocker; F1 (shared mutable state on the
  documented singleton, against the B6 precedent) and F2 (unverified causal
  attribution) are. Both are confined to the status *string* — no data-path
  count is affected — but the loop's stopping condition is no MEDIUM-or-higher
  finding, and F1 is a ~mechanical fix (return the tuple, update call sites).
- Re-sweep the F1/F2/F3 fix commits as their own range (`4ff7421..HEAD` after the
  fix) before re-gating.
