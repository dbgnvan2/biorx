# Implementation plan — search within results (2026-10-07, SW1–SW8)

**Request:** "I want to be able to search — sub search — through the returned
result set. Above the Results allow the user to add new terms and search
through the result set. Can this be done efficiently as it's like
{previous search terms} AND {New Term}"
**Status:** PLAN — awaiting owner approval. No code written.
**Retrieval layer untouched:** no change to any `PROTECTED` file.

---

## 0. Can it be done efficiently? Yes.

A finished search keeps every matched paper in memory on the server
(`job.result`, `web/routes_searches.py`); the page fetches it 50 at a time
(`GET /api/searches/{id}/results?offset&limit`). A sub-search is a filter over
that list with the rule every result already passes (`src/filtering.py`
`term_matches`): no source is asked again, and 2,000 papers × a few terms is a
few milliseconds.

It is exactly {previous search} AND {new term}, **over what the search
returned**. Limits that follow from that, shown on the page:
- A paper the first search did not return (past the max-results cap, default
  200 per source, ceiling 2,000) cannot be found by a sub-search. The page
  says "Searching within the N results of this search".
- A search job expires like today (410 → "run it again").

Rejected alternative: re-running the source search with the term added to the
filter. It would reach past the cap, but costs a full search (minutes with
bioRxiv) per term.

## 1. Behaviour

- Above the results: a box **"Search within these results"** and an **Add**
  button (Enter works). Same syntax as every box: commas = any of these,
  `AND` = all of these, `*` = word start, several words = exact phrase.
  Matched against title or abstract.
- Each added term becomes a chip with ×. Chips combine with AND: the shown
  results contain term 1 **and** term 2 … Removing a chip widens again.
- A count line: "Showing 37 of 183 results (within: cortisol; infant*)".
  Zero → "No results contain …" with the chips still there to remove.
- Changing the chips goes back to page 1 and clears ticked papers (the
  notice says so), because ticks on papers that are no longer shown would
  otherwise be saved unseen.
- **"Save all N results"**, the summaries list and the summaries PDF act on
  the refined set when chips are present (N is the refined count). The default
  list name adds the terms: "sleep – within cortisol – 2026-10-07".
- A new search clears the chips.

## 2. Design

- `src/filtering.py`: `within_matches(paper, terms)` — every term must match
  title-or-abstract via `term_matches`. Pure.
- `web/routes_searches.py`: `_refined(job, within)` used by
  `GET …/results`, `POST …/save-as-list` (when `paper_ids` is null),
  `GET …/summaries` and `POST …/summaries.pdf`. `within` is a repeated query
  parameter on GETs and a list field on POST bodies; at most 10 terms of at
  most 200 characters (422 otherwise). The results payload gains
  `total_unrefined` and echoes `within`.
- `web/static/app.js`: `state.within` (list); `resultsUrl(jobId, offset, limit,
  within)` pure builder with `encodeURIComponent`; chips built with
  createElement/textContent; `addWithinTerm`, `removeWithinTerm`.
- No new background work, so no new disabled-button rule applies; the Add
  button is disabled while a page request is in flight (no double add).

## 3. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| SW1 | `within_matches`: every term must match; commas OR inside a term; AND inside a term; adversarial (P7): a paper with "cortisol" in neither title nor abstract (only in the journal name) is excluded. | `tests/test_filtering.py::test_sw1_*` |
| SW2 | `GET …/results?within=a&within=b` returns only papers matching both; `total` is the refined count, `total_unrefined` the full count; paging (offset/limit) is over the refined list; no `within` → identical to today. | `tests/web/test_searches_routes.py::test_sw2_*` |
| SW3 | No source is called by a sub-search (orchestrator fake records zero calls after the job finished). | `test_sw3_within_makes_no_source_calls` |
| SW4 | Save all with `within` saves only refined papers; ticked `paper_ids` still win. | `tests/web/test_filter_test_route.py::test_sw4_*` (save-as-list tests live there) |
| SW5 | Summaries list and PDF honour `within`. | `tests/web/test_searches_routes.py::test_sw5_*` |
| SW6 | Validation: >10 terms or a term >200 chars → 422; an empty or only-"AND" term is ignored. Real-scale (P9): 2,000-paper job, 3 terms, refined in < 0.5 s. | `test_sw6_*` |
| SW7 | Page: adding a chip resets to page 1, clears ticks with a notice, builds the URL with each term encoded; removing restores; a new search clears chips; count line text; chips are buttons with textContent. | `tests/web/test_frontend_wiring.py::test_sw7_*` (node-run, not source greps) |
| SW8 | Page: Save button label and default list name use the refined count/terms. | `test_sw8_*` (node-run `saveButtonLabel`, `defaultListName`) |
| SW-R | Existing search, save-as-list, summaries and page tests pass unmodified; CI green. | full suite |
| SW-L | Live: on production, run a search, add two terms, check the counts against a manual count of the shown titles, save the refined set and check the list. | **Human/browser check** (needs a signed-in browser) |

## 4. Build order

1. SW1 → `within_matches`.
2. SW2, SW3, SW6 → results route.
3. SW4, SW5 → save-as-list and summaries.
4. SW7, SW8 → page.
5. Docs (CHANGELOG, spec coverage), full suite, `/chdp` Hermes gate, push, CI, SW-L.

## 5. Owner decisions (defaults chosen)

- **Field:** title-or-abstract only. A Title/Abstract choice could be added
  later; not asked for.
- **Ticks cleared** when the chips change (reason in §1).
- **Save all / PDF follow the refinement** — what is on screen is what is saved.
