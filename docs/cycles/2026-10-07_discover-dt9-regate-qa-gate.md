# Learning-QA re-gate — Discover Terms DT9, 2026-10-07 (fix loop 1)

**Verdict: APPROVED (0 findings)**

Binary: APPROVED. The previous gate (`2026-10-07_discover-dt9-qa-gate.md`)
REJECTED with F1 (medium, billing) and F2 (low, test). `c1280d7` fixes both.
This re-gate verifies the two fixes, mutation-checks their regression tests,
re-sweeps the whole range including the fix commit (the only unreviewed code),
and re-runs the full suite. Both fixes are correct and their tests are
provably-failing; no new findings. Full suite green.

---

## Report

RANGE:       `git diff origin/main...HEAD` (3 commits, HEAD = c1280d7). Three-dot
             identical to two-dot here (origin/main is an ancestor). Materialized
             to /tmp/sweep-regate.diff (1063 lines, 13 files); reproducible via
             `cd /Users/davemini2/ProjectsLocal/biorx && git diff origin/main...HEAD`.
             The previous gate reviewed 72b480d..547dec2; this re-gate re-sweeps
             that range plus the fix commit c1280d7.

COMMITS:     72b480d docs: plan DT9 — Discover offers only terms that find papers
             547dec2 feat: Discover offers only terms that find papers (DT9)
             c1280d7 fix: a replacement call that never reached the model keeps
                    the cost counted (addresses F1 and F2)

APPLICABLE:  P1, P2, P5, P6, P19, P26, P27, P28.

CHECKED:     P1, P2, P5, P6, P19, P28 (verified in the prior gate; unchanged in
             this range and re-confirmed). P26/P27/P6 re-checked against the fix
             commit specifically, since fix commits are the least-reviewed code.

## Fix verification

F1 (P6/P2, billing demotion) — web/routes_discover.py:159
  Old: `if carried is not None:` — adding an LLMError's UNCOUNTED via
  `TokenUsage.__add__` demoted the first call's known 110 tokens to
  `counted=False` ("cost not reported").
  New: `if carried is not None and carried.counted:` — only a reported cost is
  added; a usage-less failure leaves the first call's counted 110 intact.
  Verified correct against the module semantics: `LLMError.__init__`
  (src/llm_providers.py:82-84) coerces a missing `usage` to UNCOUNTED, and
  `TokenUsage.__add__` (src/tokens.py:42-53) returns `counted=self.counted and
  other.counted`, so adding UNCOUNTED was exactly the demotion the gate flagged.
  Regression tests (exact-value, not floors):
    test_dt9b_replacement_that_never_reached_the_model_keeps_the_cost_counted
      asserts recorded == [TokenUsage(100, 10, 110, True)] for a
      ProviderUnavailableError("down") carrying UNCOUNTED.
    test_dt9b_replacement_that_cost_tokens_adds_them
      asserts recorded == [TokenUsage(150, 10, 160, True)] for a
      ProviderResponseError carrying TokenUsage(50, 0, 50, True).
  MUTATION CHECK (P27/P37): reverting the guard to `if carried is not None:`
  makes test_dt9b_replacement_that_never_reached_the_model_keeps_the_cost_counted
  go RED (TokenUsage(...,counted=False) != TokenUsage(...,counted=True)); the
  tree was restored and re-verified clean.

F2 (P27, source-grep test) — tests/web/test_discover_routes.py:745
  The `inspect.getsource(routes_discover)` assertion is removed. Its behavioural
  half (check_terms calls on_progress) remains as test_dt9f_phase_names_the_check,
  and a new test test_dt9f_job_phase_shows_the_check runs a full job and asserts
  body["phase"] == "Checking terms in Europe PMC (2 of 2)", proving the route
  actually passes `progress` into check_terms.
  MUTATION CHECK: dropping `progress` from the check_terms call at
  web/routes_discover.py:137 makes test_dt9f_job_phase_shows_the_check go RED
  (phase stays "Asking anthropic (2 papers found)"); tree restored and re-verified
  clean.

## Sibling scan (P5/P6 meta-rule — grep for the same class)

The F1 bug was `job.token_usage + <UNCOUNTED>` demoting a counted value. The only
other exception-carried-usage site, BilledCall.__exit__ (src/spend.py:143-146),
uses ASSIGNMENT under a `not self.job.token_usage.counted` guard — it replaces an
uncounted value with a counted one and never runs `+`, so it cannot demote a
counted value. No sibling shares the F1 class. The replacement round's own
parse_terms(DiscoverParseError) path is safe: usage2 was already added before
parse_terms runs, and DiscoverParseError has no `.usage` (getattr → None).

## TEST GATE

`venv/bin/python -m pytest tests/ -q` → exit 0
(1661 passed, 2 skipped, 4 warnings, ~70 s; up from 1658 in the prior gate by the
3 new tests: two F1 billing tests + one F2 phase test). The 2 skips are
CI/platform-only (RLIMIT_AS on Linux; Python 3.12 version check); neither is a
node-run page test. Node frontend tests (test_dt9e_*, incl. the dropped-terms and
old-result cases) executed and passed. Judged by exit code (P24).

## Non-blocking observation (not a finding)

The F1 comment says UNCOUNTED "means the call never reached the model". That is
true for the failure paths this route can hit (NoLLMCredentialError,
ProviderUnavailableError), but an LLMError carrying UNCOUNTED more precisely
means "no cost was reported" — a ProviderResponseError over a non-JSON 200 body
also carries UNCOUNTED though the model was reached. The behaviour is correct
either way (never add an unknown cost, never demote a known one), so this is a
comment-precision note only; no code change required.

---

## Verdict

0 findings against P1–Pn (7 applicable). F1 (medium) and F2 (low) from the prior
gate are fixed, regression-tested with exact values, and mutation-verified. The
fix commit itself was re-swept and is clean. Full suite green.

APPROVED.
