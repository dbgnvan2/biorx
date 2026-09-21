# QA Gate — Session token meter (M1.C) + Saved References export (M2, M6)

- **Date:** 2026-09-20
- **Range reviewed:** `a0a30cf..04264a7` (2 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep
- **Test suite (clean checkout of 04264a7):** `venv/bin/python -m pytest tests/ -q`
  → **1203 passed, 1 skipped**

RANGE:       a0a30cf..04264a7 (caller-supplied)
COMMITS:     2 commits:
             1b144f7 feat(tokens): session token meter in the header (M1.C)
             04264a7 feat(references): Select All, and Save as CSV, RTF or PDF (M2, M6)
APPLICABLE:  P5 (sibling calls), P19 (producer/consumer drift), P2 (silent drop),
             P10 (hard case untested), P22 (stale branch on refactor), P21 (built-
             not-wired), P29 (exact route count)
CHECKED:     P2, P5, P10, P19, P21, P22, P29 — read src/user_store.py, src/tokens.py,
             web/auth.py, web/routes_usage.py, web/routes_references.py,
             src/reference_export.py, src/summary_pdf.py, web/static/app.js end to
             end; ran the suite on a clean worktree; empirically verified the
             cookie timestamp shape and the RTF escape round-trip through textutil.
NOT COVERED: logic correctness outside the meter/export, full race analysis, auth
             beyond the route-level check, the concurrent uncommitted M5 work in the
             working tree (see note below).

---

## The five concerns — answered

**(a) Sibling-path class (weighted heavily, per instruction).**
Two LOW findings below, both this class, both cosmetic/latent — NOT the hidden-
failure shape the prior gate rejected for (real billed spend left unrecorded).
- `register_unicode_font` is shared by the only two FPDF documents (`summary_pdf`,
  `reference_export`); grep shows no third PDF generator.
- `reference_export.safe_cell`/`to_csv` are the only CSV/export renderer; the old
  inline `_safe_cell` in `export.csv` was removed, not left as a parallel copy.
- No spend path is inconsistent: the only model-calling routes (summaries,
  discover) both write via `record_spend` (verified in the prior gate), and the
  front end refreshes the meter from both entry points (`startSummary`,
  `pollDiscover`) plus `showApp` and clears it on `signOut`.

**(b) `session_token_totals` tz comparison — SOUND.**
itsdangerous 2.2.0 `loads(..., return_timestamp=True)` returns a tz-**aware** UTC
datetime (`datetime(..., tzinfo=UTC)`). `_sqlite_timestamp` converts aware values to
naive UTC and formats `%Y-%m-%d %H:%M:%S`, matching `CURRENT_TIMESTAMP`, so the
`created_at >= since_text` string comparison is chronologically correct for the
cookie timestamp and for every other caller. Verified empirically, not just read.

**(c) RTF escaping — SOUND.**
`rtf_escape` escapes `\ { }` first (structural), drops control chars `< 0x20` and
maps `\n` → `\line `, and writes non-ASCII as signed-16 `\uN?` (code `< 0x8000`
positive, else `code - 0x10000` negative; astral plane as a surrogate pair). The
boundary is correct (max positive is 0x7FFF, so the `code < 0x8000` branch is
right, not off by one). I round-tripped euro (U+20AC), CJK negative (U+8BED) and
positive (U+4E2D), astral emoji/plane (U+1F600/U+2708) and a combining accent
through `textutil` (the Cocoa engine TextEdit/Pages use): returncode 0 and every
character survives. Braces stay balanced. No input produces a structurally
invalid document. Residual gap: the committed suite's real-reader test covers only
the positive-`\uN` cases; the negative/astral encodings are asserted by a shallow
`count("\\u") == 2`, not a round-trip (LOW, below).

**(d) Save-route authorization / subset leak — SOUND.**
`_get_list_or_404(ctx, user_id, list_id)` scopes ownership first (foreign list →
404, not 403). Items come from `list_reference_items(db, list_id)` — already
scoped to this list — and the `item_ids` filter only narrows that set, so a subset
cannot leak another user's items. `test_m6a1_save_is_per_user` covers cross-user
404 for all three formats. `_parse_item_ids` garbage → 400. `safe_filename`
reduces names to `[A-Za-z0-9-_ ]` (no CRLF, quote, or non-ASCII), so the
Content-Disposition header cannot be injected.

**(e) Meter leak / sign-out survival — SOUND.**
Server keys `session_token_totals` by `user_id` (from `current_user`);
`test_m1c1_another_users_tokens_are_never_included` pins a 999999-token foreign row
out. The window is the cookie issue time, so a new sign-in gets a new window and
the old cookie is deleted by `DELETE /api/session`. `signOut` clears the DOM meter
before `showGate`, so the next user cannot inherit the figure; a failed sign-out
leaves the correct (still-signed-in) figure. An unreadable timestamp returns
zeros, never a lifetime total (`test_m1c1_an_unreadable_cookie_reports_zero_not_a_lifetime_total`).

---

## Findings (ranked)

**1 · P5/P19 · web/routes_references.py:54 · LOW**
`_safe_filename` is a hand-maintained copy of the new
`reference_export.safe_filename`, left behind for `export_summaries_pdf`
(line 247), which is the only route not using the shared function. The copies
differ: `safe_filename` adds `.strip() or "references"`, `_safe_filename` does not.
*Impact today is nil* — list names are `.strip()`-ed at creation
(`user_store.create_reference_list`), so the diverging inputs (leading/trailing
whitespace, whitespace-only) are unreachable; both return identical strings for
every stored name. It is a latent drift, not a live bug: the first caller to pass
an unstripped name will produce `"  .pdf"` from one route and `"references.pdf"`
from the other.
*Fix (backlog):* point `export_summaries_pdf` at `reference_export.safe_filename`
and delete `_safe_filename`, so the predicate has one source; add a test asserting
the two filenames agree on edge-case names.

**2 · P5 · web/routes_references.py:129 · LOW**
`save_list` lacks the empty-subset guard its sibling has: `export_summaries_pdf`
raises 400 "None of the ticked papers are in this list" when `item_ids` matches
nothing (line 237), but `save_list` silently renders an empty document for the
same input. The front end cannot produce it (it sends item_ids from rendered
checkboxes), so this is malformed-request handling, not a reachable data loss, and
an empty file is visible rather than hidden. Still, the two `item_ids`-subset
routes disagree on the same semantic.
*Fix (backlog):* mirror the summaries.pdf 400 guard in `save_list`, or record the
intentional empty-file behaviour in a test so the asymmetry is a decision.

**3 · P10 · tests/web/test_reference_export.py · LOW (test-coverage note)**
The real-reader RTF round-trip (`test_m6a2_a_real_rtf_reader_parses_it`) exercises
only positive-`\uN` characters (Σ, ï, curly quotes); the signed-16 negative range
(0x8000–0xFFFF) and astral surrogate pairs — the two genuinely tricky paths — are
asserted only by structure (`count("\\u") == 2`), not against a reader. I verified
both round-trip correctly by hand, so this is a hardening gap, not a defect.
*Fix (backlog):* extend the textutil round-trip with a U+8BED (negative) and
U+1F600 (astral) case.

---

## Test-suite note (important context, not a gate failure)

The suite as committed is green: on a clean `git worktree` checkout of 04264a7 it
runs **1203 passed, 1 skipped**. The live working tree currently carries
**uncommitted M5 work** — `web/routes_usage.py` gains `GET /api/usage/estimate`,
`src/tokens.py` +93 lines, `tests/web/test_auth.py` bumped to
`PROTECTED_ROUTE_COUNT = 35`, plus JS/HTML/llm_config changes — which is why
`test_no_route_can_be_reached_without_a_session` fails against the working tree
(mid-flight: the route landed before its count bump). That work is outside this
range and outside this verdict; it is noted so the caller does not read the
working-tree red as a defect in these two commits.

## Verdict: APPROVED

All five concerns are sound — the cookie-timestamp comparison is correct against
the tz-aware value itsdangerous actually yields, the RTF escaping round-trips
every hard character class through a real reader, and authorization/leak/sign-out
are all pinned by tests. The sibling-path class the prior gate rejected for (billed
spend left off a sibling route) does not recur: both spend routes and both PDF
documents share their common code, and the meter refreshes from both spend entry
points. Three LOW findings remain, all cosmetic/latent or test-hardening, none a
hidden failure — recorded above for the backlog rather than fixed in-loop.
