# Plan — web accounts: name + PIN, recovery code, merge duplicate users
**Date:** 2026-09-18
**Status:** approved in chat 2026-09-18 ("do option 1 and merge the two daves and option 2")

## Problem

Every sign-in created a new user; identity was only a 30-day cookie. A cleared
cookie, another browser, or signing out left a user's filters and Saved
References unreachable. Dave's test database already has two "dave" users.

## Design

- **Account = name + PIN.** Name: 2–40 characters, unique ignoring case and
  surrounding spaces. PIN: at least `LOGIN_PIN_MIN_LENGTH` (default 6)
  characters. The shared access code is still required first.
- **Create vs sign in are separate buttons**, so a mistyped name does not
  silently create an empty account.
- **Stored hashed** with scrypt and a per-secret salt; compared in constant time.
  An unknown name costs the same hash work as a wrong PIN, and gets the same
  message ("That name or PIN is not right").
- **Lockout:** `LOGIN_MAX_FAILURES` (default 5) wrong PINs or recovery codes lock
  the account for `LOGIN_LOCK_MINUTES` (default 15) → 429 with the time left.
  Needed because the access code is shared: without it, anyone holding the code
  could guess PINs.
- **Recovery code** (option 2): shown once when an account is created or its
  PIN is set; stored hashed. "Forgot PIN" takes name + recovery code + new PIN,
  signs in, and issues a new recovery code (the old one stops working).
- **Existing cookie-only users** keep working. Settings → "Your account" lets
  them choose a name and PIN for the account they are in (claim), which also
  shows a recovery code.
- **Merge** (one-off, `python -m src.accounts merge`): moves filters, Saved
  References, usage and summary attribution from one user into another in one
  transaction; name clashes get " (merged)"; the source user is marked
  `merged_into`, and a cookie for it resolves to the target, so nobody is
  signed out mid-session. Nothing is deleted.

## Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| AC1 | Create account with name + PIN; signing in again with the same name + PIN (any case/spaces in the name) returns the same user and their filters. | `tests/web/test_accounts.py::test_ac1_*` |
| AC2 | Wrong PIN, unknown name: 401 with the same message; create with a taken name: 409. | `test_ac2_*` |
| AC3 | PIN and recovery code are never stored in plain text (scrypt hashes; the raw values do not appear anywhere in the users row). | `test_ac3_*` |
| AC4 | After LOGIN_MAX_FAILURES wrong PINs the account is locked (429), even for the right PIN, until the lock expires; a success resets the count. | `test_ac4_*` (injected clock) |
| AC5 | Recovery: name + recovery code + new PIN signs in and returns a new code; the old code no longer works; wrong codes count toward the lockout. | `test_ac5_*` |
| AC6 | A cookie-only (legacy) user can claim a name + PIN; the claimed name then signs into that same user with their data. | `test_ac6_*` |
| AC7 | Merge moves filters, lists (with items), usage and summary attribution; clashing names get " (merged)"; a cookie for the merged user resolves to the target. | `test_ac7_*` |
| AC8 | The sign-in page has name, PIN, Sign in, Create account, and Forgot PIN; the recovery code is shown in a dialog after create/recover/claim. | `tests/web/test_frontend_wiring.py::test_ac8_*`; browser check |
| AC9 | Thresholds come from env (`LOGIN_MAX_FAILURES`, `LOGIN_LOCK_MINUTES`, `LOGIN_PIN_MIN_LENGTH`), documented in `.env.example`. | `test_ac9_*` |

## Data change
`users`: `login_name` (unique index on lower(login_name)), `pin_hash`,
`recovery_hash`, `failed_logins`, `locked_until`, `merged_into` — added by the
existing `_add_column_if_missing` migration.

## Not in scope
- Per-person invite codes (the shared ACCESS_CODE stays).
- Changing a PIN while signed in (use Forgot PIN, or ask for it).
