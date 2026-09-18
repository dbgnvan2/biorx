# Plan — personal access codes (code + PIN, no name)
**Date:** 2026-09-18
**Status:** implemented 2026-09-18 (865 passed). Approved in chat ("go ahead").
**Request:** "I want each user have their own code." / "The codes are NOT a big
secret — they expire after 6 months by default. Write them to a config file so I
can read them easily. Users will forget them." / "I want this to be easy to use.
Why use a code and a PIN? Can't you display the code and ask for a PIN?"

## Problem

One shared `ACCESS_CODE` lets anyone who has it create any number of accounts,
each able to spend the owner's API key (25 summaries a day). You cannot see who
has it, and cutting one person off means changing it for everyone.

## Why a PIN as well as the code

The code is not secret: it sits readable in a file, gets written down, and is
remembered by the browser. So the code says **who** you are (it replaces the
name), and the PIN proves **it is you**. Without a PIN, anyone who saw someone's
code could use their account and their share of the API key.

## What the user sees

- **First time:** type the code you were given, then choose a PIN (6+ characters).
- **Same device later:** the page shows "Welcome back, Alice (K7QM-…)" and asks
  only for the PIN. Usually not even that: the sign-in lasts 30 days.
- **New device:** type the code and the PIN.
- **Forgot the PIN:** ask the owner. They run
  `python -m src.access_codes reset-pin --for "Alice"`, and Alice chooses a new
  PIN next time she enters her code. No recovery code to keep.
- **Forgot the code:** ask the owner, who reads it from the file.
- "Not you? Use a different code" link, for shared computers.

## Design

- **Codes in a plain file you can read.** `DATA_DIR/access_codes.yaml` (or
  `ACCESS_CODES_FILE`) — the data folder, so it survives redeploys; git-ignored; `access_codes.example.yaml` committed.
  ```yaml
  # Personal access codes. One per person. Readable on purpose.
  codes:
    - code: K7QM-3RTX-9WPA
      for: Alice
      created: 2026-09-18
      expires: 2027-03-17      # optional; default created + ACCESS_CODE_DAYS
    - code: B4ZN-HT2C-M8QE
      for: Dave
      account: dave            # ties the code to an existing account
      disabled: true           # optional; turns this person off
  ```
- **Commands:** `add --for NAME` (appends and prints a code), `renew --for NAME`
  (new expiry, same code), `reset-pin --for NAME`, `list` (code, who, expiry,
  status: new / active / expired / disabled). Hand edits work too; any code of
  10+ letters/digits is accepted (raised from 8 after review: the lookup is
  public and has no per-IP limit yet).
- **Expiry:** 180 days by default (`ACCESS_CODE_DAYS`).
- **One account per code.** The first sign-in with a new code creates the
  account (named from `for:`) and asks for a PIN. The code then belongs to that
  account.
- **Typing is forgiving:** case, spaces and dashes in the code are ignored.
- **The PIN is protected as now:** scrypt hash, 5 wrong tries lock the account
  for 15 minutes.
- **Cut someone off:** `disabled: true` or delete the entry. Sign-in is refused
  and their open session stops on the next click (each request checks the code
  is still valid). An expired code does the same, with "Your access code has
  expired — ask the owner to renew it." Nothing of theirs is deleted.
- **File changes apply without a restart** (re-read when its modified time changes).
- **Bad entries are reported, not dropped silently:** duplicate, too short,
  unreadable date, missing `for:` → that entry is skipped and named in a warning
  at startup.
- **Existing accounts** (your "dave"): give them a code with `account: dave`;
  they keep their current PIN. The shared `ACCESS_CODE` and name sign-in keep
  working for existing accounts until you remove `ACCESS_CODE` from `.env`;
  nothing new can be created with them.
- **Removed from the page:** the name field, "Create account" (a new code
  creates the account), and the recovery-code screens. The recovery code still
  works for accounts that already have one, through the old route, until the
  switch-over ends.

## Acceptance criteria and tests

All in `tests/web/test_access_codes.py` unless stated.

| ID | Criterion | Test |
|---|---|---|
| PC1 | The file loads; default expiry is created + `ACCESS_CODE_DAYS` (180 if unset or not a number, with a warning). | `test_pc1_file_loads_with_default_expiry` |
| PC2 | Bad entries (duplicate, too short, bad date, missing `for`) are skipped and each is named in a warning; good entries still load. | `test_pc2_bad_entries_are_reported` |
| PC3 | A new code + PIN creates the account named from `for:` and ties the code to it; the same code afterwards needs that PIN. A new code with no PIN answers "choose a PIN". | `test_pc3_new_code_creates_account_with_pin` |
| PC4 | Known code + right PIN signs in; wrong PIN and unknown code get the same message; lockout after 5 wrong PINs. Case, spaces and dashes are ignored. | `test_pc4_code_and_pin_sign_in` |
| PC5 | Expired, disabled or deleted code: sign-in refused with the plain reason, and an open session gets 401 on the next request. Renewing restores both. | `test_pc5_cut_off_and_renew` (injected clock) |
| PC6 | `reset-pin` clears the PIN; the next sign-in with the code asks for a new PIN, and the old one no longer works. Data is kept. | `test_pc6_reset_pin` |
| PC7 | `account:` ties a code to an existing account, which keeps its PIN and data; an unknown account name is a warning. | `test_pc7_existing_account_keeps_pin_and_data` |
| PC8 | Shared `ACCESS_CODE` + name + PIN still signs in existing accounts while set, and cannot create accounts; when unset, only personal codes work. | `test_pc8_switch_over` |
| PC9 | Two first sign-ins with one new code at the same moment: exactly one account is made (one conditional INSERT). | `test_pc9_first_use_is_atomic` |
| PC10 | Editing the file takes effect on the next request without a restart. | `test_pc10_file_change_picked_up` |
| PC11 | `add`, `renew`, `reset-pin`, `list` work; hand-written comments in the file survive `add` and `renew`. | `test_pc11_cli` |
| PC12 | `access_codes.yaml` is git-ignored; the example file exists; `.env.example` documents `ACCESS_CODES_FILE` and `ACCESS_CODE_DAYS`. | `test_pc12_ignored_and_documented` |
| PC13 | Page: code + PIN only; on a device that remembers the code it shows "Welcome back, NAME" and a PIN box; "Use a different code" clears it; a new code shows "Choose a PIN" with a confirm box. | `tests/web/test_frontend_wiring.py::test_pc13_*`; browser check by Dave |
| PC14 | The "Welcome back" name comes from a public lookup that answers only for a valid code, and says nothing more than the name and whether a PIN is set. | `test_pc14_lookup_reveals_only_name` |
| PC15 | Route table: the lookup route is the one new public route, listed with its reason in `test_auth.py::PUBLIC`. | `tests/web/test_auth.py::test_no_route_can_be_reached_without_a_session` |

Tests that change on purpose: `conftest` signs in with a code from a temporary
codes file; the name/"Create account" wiring tests move to the switch-over case.

Not code-testable: that the right person gets the right code.

## Implementation order

1. `src/access_codes.py`: load/validate/watch the file, CLI (PC1, PC2, PC10, PC11). Tests first.
2. DB: `access_code_bindings` (code_key UNIQUE, user_id, bound_at).
3. Routes: `POST /api/session` takes code + PIN; `POST /api/session/lookup`;
   per-request code check in `current_user` (PC3–PC9, PC14, PC15).
4. Front end (PC13). 5. `.gitignore`, example file, `.env.example`, README, CHANGELOG (PC12).
6. Give existing accounts codes. 7. learning-qa review, then /csdp.

## Adjacent issues found, not fixed

- No per-IP rate limit on sign-in or the new lookup (lockout is per account).
  Codes are 60 bits, so guessing them is not practical, but a limit is worth adding. (TODO)
- Any signed-in user can overwrite the shared summary for a DOI (already in TODO).
- Per-user spend is not visible in the web app (usage plan, not yet approved).

## Not in scope

A web admin page for codes; emailing codes; changing the 25-a-day cap.

## Accepted risks (Dave's choice of readable codes)

- **PIN reset window.** After `reset-pin`, whoever enters the code first sets the
  new PIN. Codes are not secret, so tell the person right after resetting.
- **Lookup tells expired/disabled apart from unknown** (403 vs 401), which
  confirms a code exists. Kept so people see "ask the owner to renew it".
- **`account:` is read only on first use.** Once a code is bound, changing its
  `account:` line has no effect (`list` shows the bound account).

## Review (learning-qa, 2026-09-18)

8 findings, all fixed with tests: impossible date / non-UTF-8 file crashed every
request (now a warning); renew could change the wrong entry or break the file
(now finds entries by any first key, keeps the indent, re-parses and leaves the
file untouched if the result is wrong); merged accounts looped at sign-in (codes
move on merge; sign-in runs the same check as each request); add to an empty
file made a dead code (header added, result verified); `disabled:` failed open
(now fails closed, unknown keys warned); a missing file was silent (logged and in
`/healthz`); writes were not atomic (temp file + rename); PIN length label was a
constant (now from `/healthz`).

## Status

| ID | Status | Proof |
|---|---|---|
| PC1 | done | `tests/web/test_access_codes.py::test_pc1_file_loads_with_default_expiry` |
| PC2 | done | `test_pc2_*` (6 tests) |
| PC3 | done | `test_pc3_new_code_creates_account_with_pin` |
| PC4 | done | `test_pc4_code_and_pin_sign_in` |
| PC5 | done | `test_pc5_cut_off_and_renew`, `test_pc5_expiry_is_checked_against_the_day` |
| PC6 | done | `test_pc6_reset_pin`, `test_pc6_reset_pin_for_an_unused_code_says_so` |
| PC7 | done | `test_pc7_existing_account_keeps_pin_and_data`, `test_pc7_unknown_account_is_refused_not_a_new_account` |
| PC8 | done | `test_pc8_switch_over`, `test_pc8_an_old_account_with_a_disabled_code_cannot_use_the_old_way` |
| PC9 | done | `test_pc9_first_use_is_atomic`, `test_pc9_the_request_that_loses_the_race_signs_in_to_the_winner`, `test_pc9_a_merged_account_keeps_working_with_its_code` |
| PC10 | done | `test_pc10_file_change_picked_up` |
| PC11 | done | `test_pc11_*` (6 tests, including the CLI run as a subprocess) |
| PC12 | done | `test_pc12_ignored_and_documented` |
| PC13 | done | `tests/web/test_frontend_wiring.py::test_pc13_*` (5); browser check on a throwaway server: code step, "Welcome, Tess Tester. Choose a PIN" with confirm box, "Welcome back, Old Dave." without it, unknown code message, old-way form, phone width. Entering a PIN and signing in were not driven by Claude — left to Dave. |
| PC14 | done | `test_pc14_lookup_reveals_only_name` |
| PC15 | done | `tests/web/test_auth.py::test_no_route_can_be_reached_without_a_session` (lookup in `PUBLIC`; protected count 31 after removing `/api/me/account`) |

Changes from the plan: `POST /api/me/account` (claim a name) removed — a code
now makes the account, and the route could overwrite a code user's PIN. Codes
file lives in `DATA_DIR`, not the project folder.
