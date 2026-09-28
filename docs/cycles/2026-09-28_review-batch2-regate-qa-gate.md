# QA Gate — re-sweep after fix loop 1 (review batch 2)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (4 commits). The fix commits are re-swept
  as `f38d893..HEAD` (2 commits: `de3c124`, `1b818aa`), per the sweep rule that a
  fix commit is unreviewed code; the whole range is then re-confirmed.
- **Reviewer:** learning-qa failure-pattern sweep (gate author verified each fix
  against the source directly; the prior cold-pass findings were the input).
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1464 passed, 1 skipped**
  (57.03 s; 4 warnings, all Starlette deprecation notices) — up 10 from the
  rejected gate's 1454, one regression test per finding.

RANGE:       origin/main..HEAD (caller-supplied), re-swept as f38d893..HEAD for the
             fix commits. Materialized to /tmp/sweep.diff (2,304 lines); the fix
             commit de3c124 was read hunk-by-hunk against src/db.py, src/llm.py,
             src/llm_providers.py, src/sources/europepmc.py, src/sources/schema.py,
             src/summarize.py and agents/summarization_agent.py.
COMMITS:     4 commits:
             eb2302d fix(summaries): review batch 2 — shared summaries are server-verified, one pipeline
             f38d893 test(drift): move the retrieval baseline past review batch 2
             de3c124 fix(summaries): batch-2 gate findings — Ollama retries, PMCID lookup, atomic guard
             1b818aa test(drift): move the retrieval baseline past the batch-2 gate fix
APPLICABLE:  P1 (transient as permanent negative — Ollama 429), P3 (narrow-scope
             exclusion — pmcid/title canonical ids), P5 (sibling robustness — the
             retry gate vs hosted clients' RETRYABLE_STATUS), P19 (producer/
             consumer drift — the summary_text convention), P22 (return-contract
             change — insert_summary None→raise), P26 (fix-commit regression),
             P28/P29 (test isolation / exact assertions).
CHECKED:     P1, P3, P5, P19, P22, P26, P28, P29. Verified each fix against the
             producer code it must mirror (RETRYABLE_STATUS shared constant;
             make_canonical_id prefixes pmid:/pmcid:/title:), traced the atomic
             downgrade guard through SQLite's changes()/rowcount semantics and a
             real two-connection test, and confirmed every new test asserts exact
             values (== call counts, == strings, == list equality), not floors.
NOT COVERED: no caller-excluded paths. Out-of-scope families per learning-qa.md:
             logic/algorithmic correctness, concurrency/races beyond the guard,
             auth/authz, injection & security, performance, dependency/supply-chain,
             API-contract compatibility, test quality, architecture. Live provider
             behaviour (how Ollama/Europe PMC actually answer) is covered by
             fixtures, not exercised live.

---

## The three rejected findings, re-verified

**Finding 1 (blocking, P5) — FIXED.** `src/llm.py:106` now branches on
`status not in RETRYABLE_STATUS` (raising `ProviderResponseError`) instead of
`status < 500`, importing `RETRYABLE_STATUS` from the sibling module
(`src/llm_providers.py:46`, `(408, 409, 429, 500, 502, 503, 504)`). A 408/409/429
now retries and, after `MAX_ATTEMPTS`, raises `ProviderUnavailableError`; genuine
client errors (400/401/403/404/422) stay a terminal `ProviderResponseError`; a
connection error (`status is None`) still retries exactly as before. The behaviour
now matches the hosted clients (`DeepSeekClient` retries `status in RETRYABLE_STATUS`
and treats the rest as `not resp.ok`). Pinned by
`test_m29_ollama_429_is_retried_then_unavailable[408/409/429/503]` (asserts exactly
3 attempts, `ProviderUnavailableError`) and `test_m29_ollama_400_is_not_retried`
(asserts exactly 1 attempt, `ProviderResponseError`).

**Finding 3 (non-blocking, P3) — FIXED.** `lookup_at_source`
(`src/summarize.py:91-95`) now extracts a `pmcid:` id the same way it did `pmid:`,
and `EuropePmcAdapter.get_by_id` (`src/sources/europepmc.py:195-196`) queries a bare
PMCID as `PMCID:<ID>` (matching `make_canonical_id`'s `pmcid:{pmcid}`,
`src/sources/schema.py:168-169`). A `title:` fingerprint with no DOI now raises a
distinct `PaperNotFoundError` saying *why* it cannot be looked up
(`src/summarize.py:137-141`), after the stored-row and candidate checks, so stored/
found papers still resolve. Pinned by `test_a1_pmcid_resolved_via_europe_pmc`
(asserts `get_by_id("PMC77")` once, exact title), `test_a1_europe_pmc_queries_pmcid_by_field`
(asserts the exact query string `PMCID:PMC77`), and
`test_a1_title_fingerprint_says_why_it_cannot_be_looked_up` (asserts the "title
fingerprint" message and that the lookup is never attempted).

**Finding 4 (non-blocking note, concurrency) — FIXED.** The check-then-insert is
now one statement (`src/db.py:770-794`): `INSERT … ON CONFLICT(paper_id) DO UPDATE
SET … WHERE COALESCE(summaries.source_text, '') <> 'full_text'`. A full-text summary
committed by another worker between the read and the write leaves the `DO UPDATE`
skipped, `changes()` reports 0 rows, `cursor.rowcount == 0`, and
`SummaryDowngradeRefused` is raised. The `COALESCE(…, '') <> 'full_text'` predicate
preserves the pre-fix semantics exactly (a `NULL`-source_text legacy row is not
treated as full text). Pinned by a real two-connection SQLite test
(`test_a1_guard_sees_a_summary_committed_by_another_connection`) asserting the
committed full-text row survives, and `test_a1_abstract_insert_on_a_new_paper_still_works`
guarding the over-broad-fix regression (abstract-over-abstract still writes).

**Finding 2 (GUI discover dialog, non-blocking) — unchanged, correctly left open.**
Recorded-not-fixed per the plan's D-decisions; the retiring GUI's `DiscoverTermsWorker`
still calls `generate` unguarded. Not touched by this fix loop, as intended.

## Re-sweep of the fix commits — no new findings

- **P26 (fix-commit regression):** none. The abstract-path upsert is behaviourally
  equivalent to the old `INSERT OR REPLACE` for every non-full_text case (new row,
  NULL-source_text row, abstract-over-abstract), and the full_text path still uses
  `INSERT OR REPLACE`. `SummaryDowngradeRefused`/`SummaryNotSaved` are plain
  `Exception` subclasses (`src/db.py:118-123`), so they are not swallowed by the
  `except sqlite3.Error` handler. The Ollama branch changes only the retry predicate,
  not the error mapping for connection failures.
- **P19 (producer/consumer drift):** the `pmcid:` and `title:` prefixes in the fix
  match `make_canonical_id` (the single producer), not a hand-copied list.
- **P28/P29:** the new tests use `tmp_path`/fixture DBs (no production files written)
  and assert exact values throughout.
- **Drift commit `1b818aa`:** moves the W1.a baseline `eb2302d → de3c124` with a
  comment naming finding 3 — the documented exception mechanism, not a silent widen.

---

## Verdict: APPROVED

The three findings that rejected the prior gate are verified fixed, each pinned by
an exact-value regression test, and the full suite is green (1464 passed, 1 skipped,
up 10 from the rejected run). The re-sweep of the fix commits found no new findings.
Finding 2 remains an explicitly recorded, non-blocking retiring-GUI regression.
