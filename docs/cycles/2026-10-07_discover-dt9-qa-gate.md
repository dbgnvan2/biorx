# Learning-QA gate — Discover Terms DT9, 2026-10-07

**Verdict: REJECTED (1 medium, 1 low — 0 high)**

Binary: REJECTED. The core objective — offer only terms that find papers, with a
failed Europe PMC count never reading as 0 (P1) — is correctly implemented and
well-tested, and the full suite is green. But one MEDIUM finding lands exactly in
the flagged focal area (billing of the second model call): when the replacement
round's model call fails without reporting a cost, the counted first call is
demoted to uncounted, so the token meter reports "cost not reported" for a call
whose 110 tokens are in fact known. This is a P6/P2 regression introduced by this
commit's new replacement round, not a pre-existing gap. It is a one-line fix plus
a regression test; re-gate after the fix.

---

## Report

RANGE:       `git diff origin/main...HEAD` (2 commits, HEAD = 547dec2). Three-dot
             used; identical to two-dot here because origin/main (3667a15) is an
             ancestor. Materialized to /tmp/sweep.diff (910 lines, 12 files);
             reproducible via `cd /Users/davemini2/ProjectsLocal/biorx &&
             git diff origin/main...HEAD`.

COMMITS:     72b480d docs: plan DT9 — Discover offers only terms that find papers
             547dec2 feat: Discover offers only terms that find papers (DT9)

APPLICABLE:  P1, P2, P5, P6, P19, P27, P28.

CHECKED:     P1 (failed Europe PMC count → None, never 0: src/discover.py:229-242
             returns None on retry exhaustion / non-200 / bad JSON, and the caller
             treats only `live[t] == 0` as "drop" at web/routes_discover.py:138,
             163-164 — so None (unchecked) is kept; covered by
             test_dt9a2_failed_count_is_none_not_zero and
             test_dt9d_unchecked_terms_are_kept_and_labelled). P5 (term check uses
             with_retry + timeout + polite User-Agent, consistent with sibling
             Europe PMC calls; rate limit spaced by check_terms' per-term sleep,
             config-driven, asserted by test_dt9f_requests_are_spaced_by_the_configured_delay).
             P19 (term-check query == the filter's own query, proven by
             test_dt9a, which records params and compares against
             build_europepmc_query of both a full page-filter dict and term_filter;
             query_builder is called, not edited). P28 (autouse offline fixture
             `_discover_term_check_is_offline_by_default` patches
             web.routes_discover._europepmc_counter to a stub; unit tests pass a
             fake `get`; the session guard fingerprints the real DB). P2 (dropped
             terms are logged and named on the page; "not checked" is surfaced).
             P6/P2 (billing flag demotion — FINDING F1 below). P27 (phase-wiring
             test is a source grep — FINDING F2 below).

NOT COVERED: The live Europe PMC request and the live model round (explicitly out
             of scope per plan DT9-L, flagged by the author). Logic/UI-correctness
             families outside the learning-qa scope. (This repo HAS
             ./LEARNINGS.md; reviewed alongside the generic P1–P35 catalogue.)

TEST GATE:   `venv/bin/python -m pytest tests/ -q` → exit 0
             (1658 passed, 2 skipped, 4 warnings, ~70 s). The 2 skips are
             CI/platform-only (RLIMIT_AS on Linux; Python 3.12 version check);
             neither is a node-run page test — node v26.7.0 is present and all
             node-run frontend tests (incl. test_dt9e_*) executed and passed.
             Deterministic; judged by exit code (P24).

Empirical probe (run against the project venv): a counted first call
TokenUsage(prompt=100, completion=10, total=110, counted=True), when the
replacement round raises an LLMError carrying no usage, produces
TokenUsage(prompt=100, completion=10, total=110, counted=False) via
`job.token_usage + carried` — the `counted` flag flips while the numbers survive.

---

## FINDINGS

F1 · P6/P2 · web/routes_discover.py:155-157 · MEDIUM (high confidence)
  The replacement round's failure handler demotes a counted first call to
  uncounted. `LLMError.__init__` (src/llm_providers.py:82-84) coerces `usage` to
  UNCOUNTED, so `carried = getattr(e, "usage", None)` is never None for an
  LLMError — the `if carried is not None` guard only excludes DiscoverParseError.
  Adding UNCOUNTED via `TokenUsage.__add__` (src/tokens.py:52) returns
  `counted=False`, so `finalize_usage` writes `tokens_counted=0` alongside
  prompt_tokens=100/completion_tokens=10, and the session meter reports "1 call,
  cost not reported" for a call whose 110 tokens are known. This under-reports the
  first call (P2) and contradicts the module's own invariant that counted=False
  means "numbers unknown, never a false record". Trigger: first call succeeds
  (counted) AND the replacement round's second call fails on a transient outage
  with no usage — a plausible path given the owner's motivating case ("cooperative
  species survival" found 0 papers, so a replacement round always runs).
  Fix: guard `if carried is not None and carried.counted` so only a reported cost
  is added, leaving the first call's counted 110 intact; add a regression test
  asserting `recorded[0].counted is True` (and total == 110) for a replacement
  round that fails with a usage-less ProviderResponseError/ProviderUnavailableError.

F2 · P27 · tests/web/test_discover_routes.py:686 · LOW (high confidence)
  test_dt9f_phase_names_the_check asserts the phase string exists in the route's
  source (`inspect.getsource(routes_discover)`), not that the `progress` closure
  is actually passed into `check_terms` at web/routes_discover.py:137. The string
  lives in the closure definition, so the wiring could be removed and the test
  would stay green. The behavioural half only exercises `check_terms` in
  isolation. Fix: capture `job.phase` during a `_run_checked` run and assert it
  contains "Checking terms in Europe PMC (1 of 2)", or assert the route passes
  `progress` into `check_terms`.

---

## Verdict note

0 high, 1 medium, 1 low. P1, P19 (query-equality), P5/P28 (rate-limit spacing and
network isolation) all verified correct. The single blocker is F1, a billing
regression in the exact area the gate was asked to scrutinise: the new replacement
round's failure path silently under-reports a counted first call. Fix is one line
plus a regression test that pins `counted is True` (exact, not a floor — the
current test asserts only `total == 110` and stays green while `counted` flips).
F2 is a low-priority test-strengthening and does not by itself block. Re-run the
full suite and re-gate after fixing F1 (and ideally F2).
