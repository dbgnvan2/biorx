# Plan — per-person invite codes
**Date:** 2026-09-18
**Status:** proposed, awaiting approval
**Request:** "I want each user have their own code."

## Problem

Today one shared `ACCESS_CODE` lets anyone who has it create any number of
accounts, and every account can spend the owner's API key (25 summaries a day
each). You cannot tell who was given the code, and cutting one person off means
changing the code for everyone.

## Design

- **An invite code per person.** You make one from the command line, with a note
  saying who it is for:
  `python -m src.accounts invite create --db PATH --note "Alice"`
  It prints the code once (format `XXXX-XXXX-XXXX-XXXX`, the same alphabet as
  recovery codes, 80 bits). You send it to Alice however you like.
- **Stored as a hash only.** Invite codes are random and long, so a SHA-256 hash
  is enough and lets the server look the code up (scrypt with a random salt
  cannot be looked up). The plain code is never stored or logged.
- **Single use.** Creating an account uses the code up; the invite records which
  user it made.
- **Expires.** Default 14 days (`INVITE_DAYS` in `.env`, or `--days` on the
  command). An unused, expired code stops working.
- **Revocable.** `invite revoke --id N` stops an unused code working.
- **List.** `invite list` shows id, note, created, expires, status
  (unused / used by NAME / expired / revoked). Never the code.
- **Creating an account needs an invite code.** The shared `ACCESS_CODE` no
  longer creates accounts.
- **Signing in needs only name + PIN.** Once someone has an account, the PIN is
  their credential (lockout after 5 wrong tries stays). `ACCESS_CODE` becomes
  optional: if it is set in `.env`, it is still asked for at sign-in and
  forgot-PIN, as an extra layer; if it is empty, the field is hidden. Existing
  accounts keep working either way.
- **Turning someone off.** `python -m src.accounts disable --db PATH --name Alice`
  (and `enable`). A disabled user cannot sign in, and their existing cookie stops
  working on the next request. Nothing of theirs is deleted.
- **Sign-in page.** Name, PIN, Sign in. "Create account" shows an extra
  **Invite code** field. The access-code field shows only when the server says
  one is set.

### Why the command line and not a web page
There is no admin role in the web app yet; adding one is its own security
change. The command works locally and in Docker (`docker exec … python -m
src.accounts invite create …`). An admin page can follow later if wanted.

## Acceptance criteria and tests

All tests in `tests/web/test_invites.py` unless stated.

| ID | Criterion | Test |
|---|---|---|
| IN1 | `invite create` prints a code in `XXXX-XXXX-XXXX-XXXX` form; the database holds only its SHA-256 hash and the note; the plain code appears nowhere in the invites row or the log. | `test_in1_invite_is_stored_hashed_only` |
| IN2 | Creating an account with a valid invite code works and returns a recovery code; the invite is marked used by that user. | `test_in2_invite_creates_an_account_once` |
| IN3 | The same invite code cannot create a second account (409 or 401 with "already used"). | `test_in2_invite_creates_an_account_once` (second half) |
| IN4 | An expired invite and a revoked invite are refused with a clear message; an unknown code is refused with the same message as a revoked one. | `test_in4_expired_revoked_unknown_refused` (injected clock) |
| IN5 | The shared `ACCESS_CODE` alone can no longer create an account. | `test_in5_access_code_does_not_create_accounts` |
| IN6 | With `ACCESS_CODE` empty, an existing account signs in with name + PIN only; with it set, sign-in without it is 401. Forgot-PIN follows the same rule. | `test_in6_access_code_optional_for_sign_in` (both cases) |
| IN7 | A disabled user cannot sign in (403, "This account is turned off"), and an existing cookie for them gets 401 on the next request; `enable` restores both. Their filters and lists are still in the database. | `test_in7_disable_blocks_sign_in_and_cookie` |
| IN8 | `invite list` shows id, note, created, expires and status, and never the code; `invite revoke` works only on unused invites. | `test_in8_cli_list_and_revoke` |
| IN9 | Invite length of life comes from `INVITE_DAYS` (default 14), documented in `.env.example`; a bad value falls back to 14 with a warning. | `test_in9_invite_days_from_env` |
| IN10 | Sign-in page: "Create account" reveals an Invite code field and sends it; the access-code field is hidden when `/healthz` reports `access_code_set: false`. | `tests/web/test_frontend_wiring.py::test_in10_*`; browser check by Dave |
| IN11 | Two people creating accounts with the same invite at the same moment: exactly one succeeds (the use is claimed in one conditional UPDATE, not read-then-write). | `test_in11_invite_use_is_atomic` |
| IN12 | Route table: no new public route except as listed; `PROTECTED_ROUTE_COUNT` unchanged (no new web routes — invites are CLI only). | `tests/web/test_auth.py::test_no_route_can_be_reached_without_a_session` |

Existing tests that change on purpose:
- `test_auth.py::test_an_unset_access_code_refuses_everyone` becomes "an unset
  access code refuses account creation without an invite" — an empty
  `ACCESS_CODE` no longer means nobody can get in, because accounts are now the
  credential.
- `conftest.account_body()` and `signed_in` create accounts through an invite.

Not code-testable: whether the codes reach the right people. That is up to you.

## Implementation order

1. DB: `invites` table (id, code_hash UNIQUE, note, created_at, expires_at,
   used_by_user_id, used_at, revoked_at); `users.disabled_at`. Added by the
   existing migration helper.
2. `src/accounts.py`: `create_invite`, `list_invites`, `revoke_invite`,
   `use_invite` (one conditional UPDATE … WHERE used_at IS NULL AND revoked_at
   IS NULL AND expires_at > now), `disable_user`/`enable_user`; `create_account`
   takes the invite and uses it in the same transaction as the INSERT.
   CLI subcommands. Tests IN1–IN4, IN7–IN9, IN11 first.
3. `web/routes_session.py` + `web/auth.py`: invite on create, optional access
   code on sign-in/recover, disabled check in `sign_in` and `current_user`.
   Tests IN5, IN6, IN7, IN12.
4. Front end: invite field, access-code field shown only when set. IN10.
5. Docs: README access section, `.env.example` (`INVITE_DAYS`), CHANGELOG.
6. learning-qa review, then /csdp.

## Adjacent issues found, not fixed

- No per-IP rate limit on `/api/session`: lockout is per account, so someone
  could try one PIN against many names. With invites, the names are fewer and
  unknown to outsiders, but a limit is still worth adding. (TODO)
- Any signed-in user can overwrite the shared summary for a DOI (already in TODO).
- The owner has no way to see per-user spend in the web app; usage is in
  `usage_events` only. (The usage/dollar-cap plan covers this if approved.)

## Not in scope

- A web admin page for invites.
- Email delivery of invite codes.
- Changing the 25-a-day cap (stays at 25).
