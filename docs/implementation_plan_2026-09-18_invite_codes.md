# Plan — personal access codes
**Date:** 2026-09-18
**Status:** proposed, awaiting approval (revised after Dave's answers)
**Request:** "I want each user have their own code." / "The codes are NOT a big
secret — they expire after 6 months by default. Write them to a config file so I
can read them easily. Users will forget them." / Code is used at every sign-in.

## Problem

One shared `ACCESS_CODE` lets anyone who has it create any number of accounts,
each able to spend the owner's API key (25 summaries a day). You cannot see who
has it, and cutting one person off means changing it for everyone.

## Design

- **One code per person, in a plain file you can read.** `access_codes.yaml`
  (path from `ACCESS_CODES_FILE`, default in the project folder). It is
  git-ignored, because the codes still let people in; an
  `access_codes.example.yaml` is committed instead.
  ```yaml
  # Personal access codes. One per person. Kept readable on purpose so you can
  # tell someone their code when they forget it.
  codes:
    - code: K7QM-3RTX-9WPA
      for: Alice
      created: 2026-09-18
      expires: 2027-03-17      # optional; default created + ACCESS_CODE_DAYS
    - code: B4ZN-HT2C-M8QE
      for: Dave
      account: dave            # binds the code to an existing account
      created: 2026-09-18
      disabled: true           # optional; turns this person off
  ```
- **Add one with a command, or by hand.**
  `python -m src.access_codes add --for "Alice"` makes a code, appends it to
  the file and prints it. You can also type an entry yourself; any code you make
  up works if it is at least 8 letters/digits.
- **Expires after 6 months by default** (`ACCESS_CODE_DAYS`, default 180).
  `python -m src.access_codes renew --for "Alice"` pushes the date out and keeps
  the same code, so nobody has to learn a new one. Editing `expires:` by hand
  does the same.
- **Used at every sign-in: code + name + PIN.** Case, spaces and dashes in the
  code are ignored when typed.
- **One account per code.** The first account created with a code is bound to
  it (recorded in the database). After that the code only signs into that
  account, so a code passed around cannot make more accounts.
- **Existing accounts.** Give each one a code with `account:` (or
  `add --for "Dave" --account dave`). During the switch-over, the shared
  `ACCESS_CODE` still signs existing accounts in if it is set; it can no longer
  create accounts. Remove it from `.env` once everyone has their own code.
- **Turning someone off:** `disabled: true`, or delete their entry. Their
  sign-in is refused, and their open session stops on the next click, because
  every request checks that the user's code is still valid. An expired code
  does the same, with the message "Your access code has expired — ask the owner
  to renew it." Nothing of theirs is deleted.
- **Changes take effect without a restart.** The server re-reads the file when
  its modified time changes.
- **Bad entries are reported, not silently dropped.** Duplicate codes, a code
  shorter than 8 characters, or a date it cannot read: that entry is ignored and
  a warning is logged and shown in the startup warnings with the entry number.
- **"Remember my code on this device"** checkbox on the sign-in page (browser
  storage), ticked by default, so people rarely need to type it.
- **Status at a glance:** `python -m src.access_codes list` shows each code,
  who it is for, the account bound to it, expiry, and status
  (unused / active / expired / disabled).

## Acceptance criteria and tests

All in `tests/web/test_access_codes.py` unless stated.

| ID | Criterion | Test |
|---|---|---|
| PC1 | The file is read: valid entries load; default expiry is created + `ACCESS_CODE_DAYS` (180 if unset or not a number, with a warning). | `test_pc1_file_loads_with_default_expiry` |
| PC2 | Bad entries (duplicate, too short, bad date, missing `for`) are skipped and each produces a warning naming the entry; the good entries still load. | `test_pc2_bad_entries_are_reported_not_dropped_silently` |
| PC3 | Create account with an unused code → works; the code is bound to that account. The same code cannot create a second account. | `test_pc3_code_creates_one_account_only` |
| PC4 | Sign-in needs code + name + PIN; a code bound to another account is refused with the same message as a wrong PIN. Typed case, spaces and dashes are ignored. | `test_pc4_sign_in_needs_the_persons_own_code` |
| PC5 | Expired or disabled code, or a deleted entry: sign-in refused with the plain reason; an existing cookie gets 401 on the next request. Renewing restores both. | `test_pc5_expired_disabled_removed_cut_off` (injected clock) |
| PC6 | The shared `ACCESS_CODE` signs in existing accounts while set, but cannot create one; when unset, only personal codes work. | `test_pc6_shared_code_switch_over` |
| PC7 | `account:` binds a code to an existing account by login name; an unknown name is a warning, not a crash. | `test_pc7_bind_existing_account` |
| PC8 | Editing the file takes effect on the next request without a restart. | `test_pc8_file_change_is_picked_up` |
| PC9 | `add` appends a valid entry and prints the code; `renew` changes only the expiry; `list` shows every entry with its status. Hand-written comments in the file survive `add` and `renew`. | `test_pc9_cli_add_renew_list` |
| PC10 | Two accounts created at the same moment with one code: exactly one succeeds (binding is one conditional INSERT, not read-then-write). | `test_pc10_binding_is_atomic` |
| PC11 | `access_codes.yaml` is git-ignored; `access_codes.example.yaml` exists; `.env.example` documents `ACCESS_CODES_FILE` and `ACCESS_CODE_DAYS`. | `test_pc11_file_is_ignored_and_documented` |
| PC12 | Sign-in page: code field labelled "Your access code", a "Remember my code on this device" box that fills the field next time, and plain messages for expired/disabled. | `tests/web/test_frontend_wiring.py::test_pc12_*`; browser check by Dave |
| PC13 | Route table unchanged: no new web routes (codes are managed in the file and CLI). | `tests/web/test_auth.py::test_no_route_can_be_reached_without_a_session` |

Tests that change on purpose: `conftest.account_body()`/`signed_in` sign in with
a personal code from a temporary codes file; `test_an_unset_access_code_refuses_everyone`
becomes "no shared code and no codes file refuses everyone".

Not code-testable: that the right person gets the right code.

## Implementation order

1. `src/access_codes.py`: load and validate the file (PC1, PC2, PC8), CLI
   `add`/`renew`/`list` (PC9). Tests first.
2. DB: `access_code_bindings` (code_key UNIQUE, user_id, bound_at).
3. `web/routes_session.py` + `web/auth.py`: code check on create/sign-in/recover,
   binding, per-request validity check in `current_user` (PC3–PC7, PC10, PC13).
4. Front end (PC12). 5. `.gitignore`, example file, `.env.example`, README,
   CHANGELOG (PC11). 6. Give your existing accounts codes. 7. learning-qa review, /csdp.

## Security trade-off (your call, noted)

Codes sit in a plain file on the server. Anyone who can read that file can see
them, but they could also read the database, so this adds little risk. A code
alone is not enough: the name and PIN are still needed, and PIN lockout stays.

## Adjacent issues found, not fixed

- No per-IP rate limit on `/api/session` (lockout is per account). (TODO)
- Any signed-in user can overwrite the shared summary for a DOI (already in TODO).
- Per-user spend is not visible in the web app (usage/dollar-cap plan, not yet approved).

## Not in scope

- A web admin page for codes. Emailing codes. Changing the 25-a-day cap.
