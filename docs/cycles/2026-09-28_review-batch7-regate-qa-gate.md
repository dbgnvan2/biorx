# QA Gate (re-gate) — Auth hardening (review batch 7), committed range

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (caller-supplied; rule 1 of the review-range order).
- **Why a re-gate:** the first gate (`docs/cycles/2026-09-28_review-batch7-qa-gate.md`)
  APPROVED the batch-7 work with one high-confidence finding (finding 1:
  `clear_llm_key` leaves `preferred_model`). That fix was then committed as
  `e8c5dca`. This gate confirms the committed range: (a) the batch-7 commit
  `5477271` is byte-identical to what the first gate reviewed, and (b) the fix
  commit `e8c5dca` — unreviewed code, per the sweep discipline — is correct and
  introduces no new finding.
- **Reviewer:** learning-qa failure-pattern sweep, one pass over the materialized
  diff plus a full trace of the fix's producers/consumers (`clear_llm_key`,
  `resolve_client`, `spend.resolve_credentials`, `put_llm_key`, `reset_pin`).
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1553 passed, 2 skipped**
  (69.4 s; 4 warnings, all Starlette deprecation notices). No failures.

RANGE:       origin/main..HEAD (caller-supplied). Materialized to
             /tmp/sweep_batch7_regate.diff (1,155 lines) and read hunk-by-hunk.
COMMITS:     2 commits:
             5477271 fix(auth): review batch 7 — sign-in is rate-limited, resets need a setup code
             e8c5dca fix(llm-key): clearing a key also clears the stale model (batch-7 gate finding 1)
APPLICABLE:  P5 (sibling robustness — the key/model reconciliation now covers the
             clear path, the save path, and the reset path uniformly), P6 (derived
             flags — `preferred_model` is a real stored column read off the DB, not
             a cached flag; the fix clears it at the source), P19/P22 (producer/
             consumer contract — the stale model was a value the key-path producer
             wrote and the provider-resolution consumer later read against a
             different provider). P1/P2/P3/P4 (the first gate's set) still describe
             the batch-7 body, unchanged and re-confirmed here.
CHECKED:     P1, P2, P3, P4, P5, P6, P19, P22. Re-read the fix commit in full and
             traced: `clear_llm_key` (src/user_store.py:105) and its two callers
             (`DELETE /api/me/llm-key` at web/routes_session.py:387 and `reset_pin`
             at src/access_codes.py) against `resolve_client`
             (src/llm_providers.py:436) and `spend.resolve_credentials`
             (src/spend.py:48). Confirmed `5477271` is unchanged from the first
             gate's review by reading its `clear_llm_key` blob (still without
             `preferred_model`), so the fix is a distinct, correctly-scoped commit.
NOT COVERED: same out-of-scope families as the first gate, unchanged and still
             standing: logic/algorithmic correctness beyond the data path;
             concurrency/races (the sign-in guard burns a rate token before
             `try_slot`; `reset_pin`'s multi-statement writes are not one
             transaction); auth/authz (the two deliberate deviations and the
             TRUST_PROXY/X-Forwarded-For spoofing tradeoff); exception-safety/
             config validation (`SignInLimiter.__init__` does unguarded `int()` on
             yaml values); performance (`MAX_TRACKED` O(n) eviction); injection &
             security classes; API-contract compatibility; test quality.

---

## What this re-gate confirms

1. **`5477271` is unchanged.** `git show 5477271:src/user_store.py` shows
   `clear_llm_key` still writing only `llm_provider`/`llm_key_ciphertext`/
   `llm_key_last4` — exactly the pre-fix shape the first gate's finding 1
   identified. Nothing in the reviewed batch was rewritten in place; the fix is
   a separate commit, as intended.

2. **`e8c5dca` is exactly finding 1, nothing more.** It touches two files:
   - `src/user_store.py` — `clear_llm_key` adds `preferred_model = ''` to the
     UPDATE, with a docstring stating the M3/M5 rationale.
   - `tests/web/test_llm_key_routes.py` — adds
     `test_m3_clearing_the_key_drops_the_stale_model`.
   No other source, test, or config lines move in this commit.

3. **The fix is correct.** `resolve_client` reads `user_model` only on the
   user-key branch (`if user_key:`), and `spend.resolve_credentials` passes
   `preferred_model` only alongside the stored key. So a cleared
   `preferred_model` has no observable effect until a new key is added — and
   when one is, the stale model is gone instead of being sent to the new
   provider. Both callers of `clear_llm_key` (owner PIN reset via `reset_pin`,
   and the user's own `DELETE /api/me/llm-key`) now leave no stale model behind,
   closing the M5-reopens-M3 sibling path.

4. **The regression test is mutation-sensitive and follows convention.** It
   asserts the exact value `preferred_model == ""` after set → DELETE → re-add
   (a different provider, no model). Reverting the single SQL field would leave
   `deepseek-chat` in the column and the `== ""` assertion would fail — the test
   cannot stay green on a revert. Its fake keys are the file's own
   `«redacted:sk-…»` placeholder, matching the M3/M8 tests added by `5477271`.

---

## Findings (re-sweep of the fix commit) — dispositioned

No new finding. The one defect the first gate found is fixed and committed, and
the fix itself is clean against P5/P6/P19/P22.

One deliberate behaviour change, judged sound and not a defect: a user who
deletes their own key and later re-adds a key for the **same** provider without
naming a model no longer keeps their old `preferred_model` (it was cleared with
the key). This is consistent — the model preference is only consulted while the
user has a key — and is the more-correct reading: a removed key no longer leaves
a ghost model behind. `test_m3_same_provider_keeps_model` still passes because it
re-adds a key over an *existing* key (PUT over PUT), which does not go through
`clear_llm_key` and correctly keeps the model.

The first gate's NOT COVERED low-severity notes (token burned before `try_slot`;
`reset_pin` not one transaction; unguarded `int()` in `from_config`) are
unchanged and remain out of scope for this batch.

---

## Verdict: APPROVED

The committed range `origin/main..HEAD` is what the first gate approved plus the
committed form of its finding-1 fix. The fix commit is correct, minimal,
mutation-pinned, and clean against the applicable patterns; the full suite is
green (1553 passed, 2 skipped). The M4 `TRUST_PROXY=1` post-deploy check from the
first gate remains the only open item and is unchanged by this re-gate.
