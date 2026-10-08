# QA Gate (re-gate) — search within a search's results (2026-10-07, SW1–SW8)

**Range:** `git diff origin/main...HEAD`
**Commits:** b5e2118 (plan), 1e7d83c (feature), 65083ff (fix for prior findings)
**Prior gate:** `docs/cycles/2026-10-07_search-within-qa-gate.md` — APPROVED with
5 LOW findings (L1–L5). This re-gate verifies the fix commit 65083ff (L1, L2, L3,
L5; L4 recorded in `TODO.md`) and re-sweeps the whole range.
**Reviewer:** learning-qa — one cold re-sweep pass (delegated, given only the
diff, range, reference files, and the prior gate doc) plus an independent
verification pass by the gate runner
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1722 passed, 2 skipped** (green)

## Verdict: APPROVED

All four claimed fixes are correct and complete, L4 is recorded in `TODO.md`
rather than silently dropped, the full suite is green (1719 → 1722: three new
regression tests, all passing), and the cold re-sweep of the entire range
returned **no findings** against the applicable failure patterns. No finding
rises to LOW or above.

---

## Fix verification (65083ff)

### L1 — default list name can exceed 200 chars — FIXED

`defaultListName` (app.js) now computes the room left for the within segment
instead of a flat 40-char slice:

- `base = ((label || "").trim() || "Search").slice(0, 150)` — hard-capped at 150.
- `tail = " – " + isoDate` — always 13 chars.
- `room = Math.max(0, LIST_NAME_MAX - base.length - tail.length)` — ≥ 37.
- `refined = terms ? (" – within " + terms).slice(0, Math.min(40, room)) : ""`.

Total is therefore pinned at exactly `base + refined + tail ≤ 200`, never 201.
The new `LIST_NAME_MAX = 200` constant is a second copy of the server limit
(`SaveAsListBody.name max_length`), but it is **guarded, not a parallel
hand-maintained copy**: `test_sw8_default_list_name_never_passes_the_server_limit`
reads the real server limit and asserts `LIST_NAME_MAX == limit` plus
`len(name) <= limit` over four adversarial cases (the exact 203-char shape from
L1 included) and the date suffix. A drift in either direction goes red.

### L2 — zero-results note drops the "of this search" scope — FIXED

`withinNote` zero branch now reads
`None of the ${unrefined} results of this search contain all of: ${terms}. …`,
carrying the searched count and the narrowing scope. `test_sw7_zero_results_note`
asserts the exact string.

### L3 — 200-char limit in two places (Python + HTML) — FIXED

`maxlength="200"` removed from `index.html`; the server now echoes
`within_max_chars` in the results payload (`routes_searches.py`), and
`loadResults` sets `$("within-input").maxLength = page.within_max_chars`.
`WITHIN_MAX_CHARS` is the single source of truth, server-driven like the
term-count limit. `test_sw6_input_length_comes_from_the_server` asserts the tag
has no `maxlength` and the DOM value equals `WITHIN_MAX_CHARS`.

### L4 — phrase can match across the title/abstract join — NOT FIXED (recorded)

`TODO.md` records L4 with the reason (consistent with the pre-existing
Title-or-abstract filter box; fix both together if it matters). Not silently
dropped.

### L5 — summaries-URL `within` wiring untested — FIXED

`test_sw5_summaries_list_url_carries_the_terms` runs the real
`refreshSearchSummaries` (not a stub) via the `_sw_run(extra=...)` mechanism and
asserts the URL is `/api/searches/J/summaries?within=cortisol&within=infant*`.

---

## Re-sweep (whole range, cold pass)

The delegated cold reviewer examined all 12 applicable patterns (repo P2, P4,
P9, P13, P14; generic P5, P9, P19, P25, P27, P29, P31) across
`src/filtering.py`, `web/routes_searches.py`, `web/static/app.js`, `index.html`,
and both SW test files, traced the refinement path, and executed the SW/pf1/b2
tests. It returned:

> VERDICT: No findings against the applicable patterns (12 examined).

It independently confirmed the stub-removal mechanism in `_sw_run` has **no
silent-stub path** — every failure mode is loud (stub not removed → URL never
pushed → assertion fails; real function not extracted → ReferenceError →
`returncode != 0` assert fails). The three new regression tests assert exact
values (URL, note text, constant equality, the ceiling the old 203-char case
violates), not floors or source-text greps — each can go red.

Non-blocking scope notes surfaced (recorded, not acted on):

- **Test-quality fragility** — `SaveAsListBody.model_fields["name"].metadata[-1]`
  assumes `max_length` is the last Field metadata entry. If it ever changes, the
  test fails loudly (not silently), so it is a maintenance nit, not a false-green.
- **Unicode length skew** — JS `.slice` counts UTF-16 code units; a label of
  astral characters is over-truncated, never overflowed past the server limit.
  Safe direction.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| SW1–SW8 | as in the prior gate | green (unchanged feature code) |
| L1 | list name ≤ 200 incl. within terms; constant cross-checked vs server | fixed + regression test, green |
| L2 | zero-results note names the searched count | fixed + exact-string test, green |
| L3 | 200-char limit server-driven, single source | fixed + 2 assertions, green |
| L4 | phrase across title/abstract join | recorded in TODO.md (deferred) |
| L5 | summaries URL `within` wiring tested | fixed + real-function test, green |
| SW-R | full suite | 1722 passed, 2 skipped |

---

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P2, P3, P4, P5, P7,
P9, P10, P13, P19, P25, P26, P27, P29, P31, P35 (prior gate) and P2, P4, P9,
P13, P14 + generic P5, P9, P19, P25, P27, P29, P31 (re-sweep).

Not covered: logic/algorithmic correctness (including the deferred L4), live
source semantics (SW-L browser check, still pending human), concurrency,
authn/authz, security beyond XSS, and performance. These are outside the
learning-qa family.

---

## Notes

- One cold re-sweep pass ran (delegated). It returned zero findings and
  independently verified all four fixes; it did not merely echo the gate
  runner's conclusions (it derived the `base=150` / `tail=13` / `room≥37`
  bound itself and probed the stub-removal regex for a silent-stub path).
- The loop's stopping condition — no finding of MEDIUM or higher, and now no
  finding at all on the re-swept range — is met.
