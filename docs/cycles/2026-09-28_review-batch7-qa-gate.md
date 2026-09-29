# QA Gate — Auth hardening (review batch 7)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — one COLD pass (a fresh subagent
  given only the diff, the range, and the three catalogue files, with no knowledge
  of how the code was written or of prior passes), plus independent verification
  by the gate author (traced `clear_llm_key` / `preferred_model` consumers,
  `resolve_client`, `spend.admit`, `read_session_nonce` / `end_sessions`, and the
  sign-in guard's slot/bucket ordering).
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1553 passed, 2 skipped**
  (69.7 s; 4 warnings, all Starlette deprecation notices). No failures.

RANGE:       origin/main..HEAD (caller-supplied; rule 1 of the review-range order).
             Materialized to /tmp/sweep_batch7.diff (1,123 lines) and read
             hunk-by-hunk; changed source files read in full context.
COMMITS:     1 commit:
             5477271 fix(auth): review batch 7 — sign-in is rate-limited, resets need a setup code
APPLICABLE:  P1 (transient→terminal — the 429/503 sign-in limits), P2 (silent drop —
             `clear_llm_key`/key handling), P3 (narrow scope — the three sign-in
             routes sharing one guard), P4/P9 (hardcoded limits — moved to
             `llm_config.yaml sign_in:`), P5 (sibling robustness — the key/model
             reconciliation across clear/save paths), P6 (derived flags —
             `pin_set`/`setup_code_required` read off the hash columns). Repo P8
             (SSRF) and P9 (XSS) not exercised (no new server-side fetch; new DOM
             insertions use `value`/`textContent`, not `innerHTML`).
CHECKED:     P1, P2, P3, P4, P5, P6. Read every changed source file in full
             (`src/sign_in_limits.py`, `src/access_codes.py`, `src/accounts.py`,
             `src/llm_providers.py`, `src/db.py`, `src/user_store.py`,
             `web/routes_session.py`, `web/app.py`, `web/auth.py`, `web/deps.py`,
             `web/static/app.js`, `web/static/index.html`) and the changed tests.
             Traced: the token-bucket + semaphore guard across all three sign-in
             routes; the M5 reset → `clear_llm_key` → re-add-key path; the M6
             nonce rotation against `end_sessions`; the M7 config/gate/healthz
             split; M8/M9 `resolve_client` and `put_llm_key`; M3 stale-model
             clearing; M10 `current_user` as a plain def. Ran the full suite green.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness beyond the data-path; concurrency/races (noted: the guard
             burns a rate token before `try_slot`, so a busy-503 still spends
             allowance; `reset_pin`'s multi-statement writes are not one
             transaction); auth/authz (the two deliberate deviations and the
             TRUST_PROXY/X-Forwarded-For spoofing tradeoff were judged on merit but
             are an auth concern, not a failure-pattern one); exception
             safety/config validation (`SignInLimiter.__init__` does unguarded
             `int()` on yaml values, unlike the guarded `_env_int` sibling);
             performance (`MAX_TRACKED` O(n) eviction); injection & security
             classes; API-contract compatibility; test quality.

---

## What the batch delivers

Every plan ID for batch 7 (M4, M5, M6, M7, M8, M9, M3, M10) is implemented and
pinned by a same-named test asserting exact values, plus one gate-found defect
fixed below.

- **M4 (sign-in rate-limited).** New `src/sign_in_limits.py`: a per-address token
  bucket (20/min from `llm_config.yaml sign_in:`) and a `BoundedSemaphore` on
  concurrent scrypt (503 when full), wired as a dependency on `/api/session`,
  `/lookup` and `/recover`. Client address from the first `X-Forwarded-For` hop
  only when `TRUST_PROXY=1`, else the socket address. Pinned by
  `test_m4_sign_in_rate_limited`, `test_m4_limit_is_per_address`,
  `test_m4_allowance_refills`, `test_m4_scrypt_concurrency_bounded`.
- **M5 (reset needs a setup code).** `reset_pin` clears the merged family's stored
  LLM keys and returns a one-time setup code (printed by the CLI); the no-PIN
  sign-in branch requires it and clears it on use. Pinned by
  `test_m5_reset_requires_setup_token`, `test_m5_reset_clears_llm_key`,
  `test_m5_cli_prints_the_setup_code`, and the amended `test_pc6_reset_pin`.
- **M6 (sign-out ends the session).** `DELETE /api/session` rotates the account
  nonce (via `end_sessions`), so a copied cookie gets 401; an already-ended cookie
  cannot sign the real user out. Pinned by `test_m6_sign_out_invalidates_copies`,
  `test_m6_an_ended_cookie_cannot_sign_the_user_out`.
- **M7 (public surface shrunk).** `/healthz` returns `{"ok": true}`; the sign-in
  page's needs (`access_code_set`, `pin_min_length`, `codes_in_use`,
  `startup_warnings`) stay public on `/api/gate`; the full config (now without
  `db_path`) moves to authenticated `/api/config`. Pinned by
  `test_m7_healthz_minimal`, `test_m7_gate_shows_only_what_sign_in_needs`,
  `test_config_reports_configuration_but_never_a_secret`.
- **M8/M9 (a user key must name a keyed provider).** `resolve_client` and
  `put_llm_key` both refuse a key for a keyless provider and a key with no
  provider. Pinned by `test_m8_key_for_keyless_provider_refused`,
  `test_m8_key_for_keyless_provider_refused_on_save`,
  `test_m9_key_without_provider_refused`.
- **M3 (stale model cleared on provider change).** `put_llm_key` clears
  `preferred_model` when the provider changes and no model is given. Pinned by
  `test_m3_provider_change_clears_model`, `test_m3_same_provider_keeps_model`.
- **M10 (`current_user` is sync).** `async def` → `def` so FastAPI runs the
  blocking SQLite/file checks in the thread pool. Pinned by
  `test_m10_current_user_is_sync`.

---

## Two deliberate deviations — judged on their merits

Both are stated in the commit message. Judged sound; neither introduces a
failure-pattern risk.

1. **M5 keeps `pin_set` (and adds `setup_code_required`) on `/api/session/lookup`.**
   The plan had said lookup stops revealing `pin_set`. Merits: `pin_set` is not a
   secret — it is "does this account already have a PIN", which the sign-in page
   needs to choose "enter PIN" vs "set a new PIN" before the PIN field is shown.
   The account-takeover the plan was actually protecting against (whoever presents
   the code first after a reset sets the PIN and inherits the account) is now
   closed by the setup code, independent of `pin_set`. `setup_code_required` is
   `setup_code_pending` = `not pin_hash and setup_code_hash`, read straight off
   the DB columns (P6 clean — it reflects a real artifact, not a cached flag).
   Accepted.

2. **M7 keeps the sign-in page's fields public on `/api/gate`; full config moves to
   authenticated `/api/config` (not `/api/me`); `/healthz` returns only
   `{"ok": true}`.** The plan had said details move to an owner-only `/api/me`.
   Merits: the only genuinely sensitive field (`db_path`) is gone from every public
   and every signed-in endpoint; the remaining config (provider, model,
   `owner_key_set`, `byo_keys_enabled`, `sources`, `full_text_finders`,
   `startup_warnings`, `codes_in_use`) is non-secret metadata the signed-in search
   page legitimately needs for *every* user (the front end's `loadSources()` reads
   `/api/config`), not only the owner. `/api/gate` leaks nothing new — the old
   public `/healthz` already called `get_orchestrator()` for the same startup
   warnings, and the codes-file read still swallows errors into `startup_warnings`.
   Accepted.

---

## Findings (cold pass, ranked) — dispositioned

1. **P5 · src/user_store.py:105 (`clear_llm_key`) · clearing the key leaves
   `preferred_model`, so M5 re-opens M3 through a sibling path · confidence: high.**
   `clear_llm_key` empties `llm_provider` and the ciphertext but not
   `preferred_model`. It is called by `reset_pin` (M5, new this batch) and by
   `DELETE /api/me/llm-key` (pre-existing). After either, the user's old
   `preferred_model` survives; re-adding a key for a *different* provider with no
   model then hits `previous_provider == ""` in `put_llm_key`, so M3's clear never
   fires, and `spend.admit` (spend.py:71-72) passes the stale model to the new
   provider — the exact "every summary fails" failure M3 was written to prevent.
   **Disposition: FIXED.** `clear_llm_key` now also sets `preferred_model = ''`.
   This is safe because `preferred_model` is only consulted when the user has a
   key (`resolve_client` ignores `user_model` on the owner-key path), so a cleared
   model has no observable effect until a new key is added. Regression test added:
   `test_m3_clearing_the_key_drops_the_stale_model` (sets deepseek + model, DELETE
   the key, re-add anthropic with no model, asserts `preferred_model == ""`).
   **Mutation-verified:** reverting the one SQL field makes the test fail
   (`AssertionError: deepseek-chat`), restoring it passes. Full suite re-run green
   (1553 passed).

No other medium-or-higher finding. The cold pass's NOT COVERED notes (guard burns
a token before `try_slot`; `reset_pin` not one transaction; unguarded `int()` in
`from_config`) are low-severity hardening notes, not defects in this batch's scope.

---

## M4 human check — post-deploy only

The plan requires confirming, after deploying behind Railway's proxy, that the
per-IP limit keys on the real client address. The code reads `X-Forwarded-For`
only when `TRUST_PROXY=1`; `.env.example` and the README deploy checklist now
document setting `TRUST_PROXY=1` behind Railway and leaving it unset when reached
directly. This cannot be exercised in-session (no deployed Railway instance);
recorded here as the open post-deploy check.

---

## Verdict: APPROVED

The one high-confidence defect the cold pass found — `clear_llm_key` leaving a
stale `preferred_model`, which let M5 re-open M3 — is fixed and pinned by a
mutation-verified regression test; the full suite is green (1553 passed, 2
skipped). P1, P2, P3, P4, P5, P6 were applicable and checked. Both deliberate
deviations (M5's `pin_set`/`setup_code_required` on lookup; M7's public `/api/gate`
plus authenticated `/api/config`) are judged sound and accepted. The fix is applied
to the working tree (`src/user_store.py`, `tests/web/test_llm_key_routes.py`) and
must be committed before push; the M4 `TRUST_PROXY=1` post-deploy check remains open.
