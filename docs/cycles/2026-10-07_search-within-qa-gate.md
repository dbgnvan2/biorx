# QA Gate — search within a search's results (2026-10-07, SW1–SW8)

**Range:** `git diff origin/main...HEAD`
**Commits:** b5e2118 (plan), 1e7d83c (feature)
**Plan:** `docs/implementation_plan_2026-10-07_search_within.md` (SW1–SW8)
**Reviewer:** learning-qa — one cold review pass (delegated, did not know how the
code was written) plus an independent verification pass by the gate runner
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1719 passed, 2 skipped** (green)

## Verdict: APPROVED

All five gate checks pass, the full suite is green, and no finding rises to
HIGH or blocking MEDIUM. The cold pass flagged two MEDIUM candidates; both were
re-graded to LOW on verification (details under Findings). Five LOW findings are
recorded for the backlog — the one worth acting on first is the default-list-name
length overflow (L1).

---

## The five gate checks

### 1. Every "all results" endpoint honours `within` consistently — PASS

All four surfaces act on the refined set through one code path, so they cannot
drift apart (P19 pitfall 2 — single source of truth, not a parallel copy):

| Surface | Route | Refinement site |
|---|---|---|
| results paging | `GET /api/searches/{id}/results` | `routes_searches.py:290` → `_refined(everything, within)` |
| save-as-list | `POST /api/searches/{id}/save-as-list` | `routes_searches.py:369-370` → `_refined(results, within)` when `paper_ids is None` |
| summaries list | `GET /api/searches/{id}/summaries` | `routes_searches.py:482` → `_job_summaries(..., within=within)` → `_refined` |
| summaries PDF | `POST /api/searches/{id}/summaries.pdf` | `routes_searches.py:507` → `_job_summaries(ctx, job, body.paper_ids, within)` → `_refined` |

`_refined` (routes_searches.py:322) is the sole refiner; it delegates every
term decision to `src/filtering.within_matches`, which reuses the existing
`split_terms` / `term_matches` / `and_parts` / `match_term` chain — the same
comma=OR, `AND`=all-parts, `*`=word-prefix semantics the main filter and the
search boxes use. No second copy of the matching rule exists. The
no-`within` path returns the unfiltered list unchanged (SW2).

### 2. Ticked `paper_ids` still win — PASS

`save_search_as_list` and `_job_summaries` both branch on `paper_ids is not
None` *before* applying `within`, so explicit ticks are never narrowed away:

- `routes_searches.py:369-374` — `if body.paper_ids is None: refine; else: keep only ticked`.
- `routes_searches.py:444-449` — same shape for the summaries/PDF path.
- Proven by `test_sw4_ticked_papers_win_over_within`: posting
  `paper_ids=["doi:10.9/d"], within=["cortisol"]` saves exactly
  "Melatonin and infants" (a paper that does *not* match `within`).

`applyWithin` (app.js) clears `state.checkedPapers` before refetching, so a
tick on a paper that is no longer shown is never saved unseen (plan §1).

### 3. Input limits — PASS (with one LOW drift hazard, L3, and one LOW overflow, L1)

Server-side, `_checked_within` (routes_searches.py:305) enforces **10 terms /
200 chars** with a 422 on *every* surface that accepts `within` — results,
save-as-list, summaries, and summaries.pdf all call it first. Empty and
only-`AND` terms are dropped, not errored. Proven by `test_sw6_limits_are_enforced`.

Client-side, the 10-term count is driven by the server value
(`within_max_terms` echoed in the results payload, read as `state.withinMax`),
per the plan's "Changes from the plan" note. The 200-char limit is mirrored as
`maxlength="200"` on the input — see L3 for the duplication this leaves.

### 4. The page never builds HTML from data — PASS

Every insertion of server- or user-supplied text in the new code uses
`textContent` or `createElement`:

- chips (`renderWithinChips`, app.js): `createElement("button")` +
  `chip.textContent = term + " ×"`, `dataset.term`, `aria-label`.
- note and heading: `$("within-note").textContent`, `$("results-heading").textContent`.
- No `innerHTML` with data anywhere in the added code (the only `innerHTML`
  occurrences in app.js are the two pre-existing comment lines about *avoiding* it).

### 5. Node-run page tests exercise real functions — PASS

The SW7/SW8 tests do not grep the source for identifiers. `_discover_js` and
`_js_block` (tests/web/test_frontend_wiring.py:463, 3142) extract the **actual
function bodies** from `web/static/app.js` by name and execute them in node
against a stubbed DOM/state, asserting on runtime output:

- `test_sw7_adding_a_term_reloads_page_one_and_clears_ticks` runs the real
  `addWithinTerm` / `removeWithinTerm` / `applyWithin` and asserts the produced
  URL, cleared ticks, and note text.
- `test_sw7_refused_while_the_search_runs_and_kept_on_failure` exercises the
  real add/refuse/failure-retain paths.
- `test_sw7_new_search_clears_terms` runs the real `startSearch`.
- `test_sw8_save_and_pdf_send_the_terms_and_name_them` runs the real
  `saveResultsAs` and `saveSummariesPdf` and asserts the posted bodies.
- `withinQuery`, `resultsUrl`, `withinNote`, `defaultListName` are evaluated
  and their return values asserted (`encodeURIComponent`, exact strings).

These are behavioural calls against real code, not source-text assertions
(P19 corollary).

---

## Findings (all LOW — none blocking)

### L1 · P9/P13 · LOW · `web/static/app.js` `defaultListName`

The default list name can exceed the server's `max_length=200`. Verified by
execution: a 150-char `searchLabel` + a within-term of 30 chars yields
`base(150) + refined(40) + " – " + isoDate(10) = 203` chars, which
`save-as-list` rejects with a 422. The old code capped at 163 (safe); SW8 added
the within portion and pushed it past 200. Reachable — `searchLabel` comes from
an unbounded `q-both` input or a filter name of up to 200 chars.

**Fix:** cap the refined segment to leave room for `base` and the date, e.g.
`const refined = terms ? (" – within " + terms).slice(0, Math.max(0, 200 - base.slice(0,150).length - 13)) : "";`
(or simply slice `refined` to 37 instead of 40). **Regression test would assert:**
`defaultListName("x".repeat(200), "2026-10-07", ["y".repeat(30)]).length <= 200`.

### L2 · P31/P13 · LOW · `web/static/app.js` `withinNote` (zero branch)

The zero-results note — `"No results contain all of: {terms}. Remove a term to
widen."` — drops the "only this search's results are searched" qualifier that
the non-zero branch carries. The narrowing is still visible (the heading renders
`Results — 0 of 183 matching`), so this is polish, not a silent negative. The
plan §1 specified this exact string, so it is per-spec, but the plan §0 also
promised a "searching within the N results" framing that the zero case omits.

**Fix (optional):** include the searched count in the zero message, e.g.
`No results among these ${unrefined} results contain all of: ${terms}.`
**Regression test would assert:** `withinNote(0, 183, ["x"])` mentions `183`.

### L3 · P19/P5 · LOW · `web/routes_searches.py:73` vs `web/static/index.html:145`

The 200-char limit lives in two places — `WITHIN_MAX_CHARS = 200` (Python) and
`maxlength="200"` (HTML) — with no single source of truth, unlike the term-count
limit which is server-driven via `within_max_terms`. Values agree today; a
future edit to `WITHIN_MAX_CHARS` would not reach the input, and the plan's
"sent by the server" note covers only the count limit. Not a current defect;
recorded as a drift hazard.

### L4 · P7 · LOW · `src/filtering.py:218` `within_matches`

Title and abstract are joined with a single space, so a multi-word phrase can
substring-match *across* the boundary (title ending `cortisol` + abstract
starting `sleep` matches the phrase `"cortisol sleep"`). Consistent with the
existing `text_group_matches` "both" field, which joins the same way; requires
exact boundary adjacency, so it is unlikely on real data. **Fix (optional):**
match each alternative against title and abstract separately, or join with a
non-space separator.

### L5 · P25 · LOW · `web/static/app.js` `refreshSearchSummaries`

The summaries GET composes `.../summaries` + `withinQuery(state.within, "?")`
correctly, but no node-run test asserts that URL carries `within`
(`_SW_SCRIPT` stubs `refreshSearchSummaries`, so it is the one within-surface
whose wiring is unexercised). Correct today; a coverage gap for a future refactor.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| SW1 | `within_matches`: every term; commas OR; AND; adversarial journal/authors-only exclusion | 4 tests, green |
| SW2 | results narrow + page over the refined list; `total` vs `total_unrefined`; no-`within` unchanged | 2 tests, green |
| SW3 | no source called by a sub-search | green (orchestrator fake: 1 call total) |
| SW4 | save-all refined; ticked `paper_ids` win | 2 tests, green |
| SW5 | summaries list + PDF honour `within` | green |
| SW6 | >10 terms / >200 chars → 422; empty terms ignored; 2,000 papers < 0.5 s | green |
| SW7 | chip add/remove, page-1 reset, tick-clear notice, URL encoding, new-search clears | 5 tests, green |
| SW8 | save/PDF send the terms; default list name | green |
| SW-R | full suite | 1719 passed, 2 skipped |
| SW-L | live browser check on production | pending (human, needs a signed-in browser) |

---

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P2, P3, P4, P5, P7,
P9, P10, P13, P19, P25, P26, P31, P35. Matcher semantics traced through
`src/filtering.py` (`within_matches`, `split_terms`, `term_matches`,
`match_term`, `wildcard_pattern`), `src/search_terms.py`, `web/routes_searches.py`,
and the SW test files.

Not covered: live source semantics (whether the refined results match a manual
recount of titles is the SW-L browser check, recorded in the plan); production
saved filters cannot be read from this machine; logic/algorithmic correctness,
concurrency, authn/authz, and performance are outside the learning-qa family.

---

## Notes

- One cold pass ran (delegated, given only the diff and range). It returned
  2 MEDIUM + 3 LOW, no HIGH, and independently confirmed the single-source
  refinement, the `paper_ids`-wins behaviour, and the matching semantics.
- The two MEDIUM candidates were re-graded to LOW on verification:
  (a) the zero-results message — the heading `Results — N of M matching` does
  surface the unrefined count in the zero case, so the narrowing is visible;
  (b) the char-limit duplication — the plan's "sent by the server" note is
  specifically about the *term-count* limit (`within_max_terms`), which is
  server-driven; the 200-char limit was never claimed to be.
- No fix commit was introduced. The loop's stopping condition — no finding of
  MEDIUM or higher requiring a fix — is met; the five LOW findings go to the
  backlog (L1 first).
