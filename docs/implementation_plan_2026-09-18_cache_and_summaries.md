# Plan — stale page in the browser; summaries that accumulate and can be saved
**Date:** 2026-09-18
**Status:** awaiting approval — no code written (updated with Dave's second report, same day)

## What Dave saw (after restarting on commit 39a6b8c)

1. "Saved searches" label still shown.
2. "Save as reference list" button does not work.
3. Each new summary replaces the previous one.
4. No way to save summaries.

## Diagnosis

- **1, 2 and 4 are a stale page in the browser.** `curl http://127.0.0.1:8000/` on
  the running server returns the new page ("Saved Filters", "Search & Browse",
  `btn-ref-export-summaries`, the summary popup). The browser shows the old one
  because the app sends no `Cache-Control` header, so the browser reuses its
  cached `index.html`/`app.js`/`styles.css` by heuristic. This was listed in
  TODO.md ("static assets have no cache-busting") and not fixed. Every future
  update will hit the same problem, for every user, until it is.
- **3 is real, by design of the last change.** The popup shows one paper at a
  time. Summaries are stored per paper on the server, but the results list does
  not show which papers have one, and there is no view of several at once.
- **4, beyond the cache:** the PDF export exists only on Saved References. From
  the Search & Browse tab there is no way to save summaries without first saving
  the results as a list.

## Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| C1 | `/`, `/static/*.js`, `/static/*.css` are served with `Cache-Control: no-cache`, so the browser re-checks on every load (a 304 when unchanged — cheap). | `tests/web/test_frontend_wiring.py::test_c1_page_and_assets_are_not_served_stale` |
| C2 | The page loads `app.js` and `styles.css` with a version query (`?v=<content hash>`), so even a browser that ignores no-cache fetches the new files after an update. | `test_c2_asset_urls_carry_a_content_hash` (hash changes when the file changes) |
| L1 | Label stays **"Saved Filters"** (confirmed by Dave). | existing `test_rn1_labels_match_the_desktop` |
| S1 | Each result row shows whether the paper has a stored summary ("✓ Summary"), found with one batch lookup per results page, not one request per paper. | `tests/web/test_summaries_routes.py::test_s1_batch_lookup_*` (new `POST /api/summaries/lookup-batch`, max 200 papers, per-paper result) |
| S2 | A **Summaries** panel on Search & Browse lists every summary for the current results, newest first; a new summary is added to the panel, never replacing another. Clicking one opens the popup. | `test_frontend_wiring.py::test_s2_*` (node-run of the panel's add/merge function: adding B keeps A) ; browser check |
| S3 | **"Save summaries (PDF)"** on Search & Browse exports the summaries for the current results (ticked papers if any are ticked, else all), same layout as the Saved References export. | `tests/web/test_summaries_pdf.py::test_s3_*` (new `GET /api/searches/{job_id}/summaries.pdf`, owner-only, only a finished search) |
| S4 | The Saved References export is unchanged and still available. | existing `test_sp1_*`–`test_sp5_*` |

## Second report (after a hard refresh) — reproduced on the running app

Reproduced at http://127.0.0.1:8000 as a separate user ("claude-check"):

- **Select-all box stays ticked across searches.** `startSearch` clears the ticked
  papers but not the header checkbox.
- **"Save to Saved References" looks broken in two ways** (the save itself works:
  41 papers saved):
  a. the button only reveals a small name box; a second click hides it again;
  b. saving the same search twice pre-fills the same name, which is refused with
     "You already have a list called …" in the top bar.
- **Summarize "hangs":** it fails in ~2 s with "Anthropic rejected the API key".
  The popup shows only "— failed"; the reason is only in the top bar. Cause: the
  `ANTHROPIC_API_KEY` in `.env` is still the doubled 216-character value (pasted
  twice); `.env` overrides the good 108-character key in the shell.
- **"Could not reach: bioRxiv/medRxiv":** `api.biorxiv.org` returns HTTP 200 with
  an empty body for every request (checked with curl, including a single-DOI
  lookup) — an outage or block on their side. The app retries 3× and reports it,
  which is correct, but (a) the web page ticks every source although
  `sources_config.yaml` marks bioRxiv and arXiv `default_selected: false`, and
  (b) the message gives no reason. Europe PMC, PubMed, PsyArXiv, SocArXiv and
  arXiv all answered in 18 of 18 test searches.

| ID | Criterion | Test |
|---|---|---|
| A1 | The select-all checkbox is cleared (unchecked, not indeterminate) when a search starts. | `test_frontend_wiring.py::test_a1_select_all_resets_on_search` |
| B1 | The save button opens a name dialog pre-filled with the default name; OK saves, Cancel does nothing. No toggle. | `test_b1_save_opens_a_dialog` (node-run with stubbed `prompt`) ; browser |
| B2 | If the name is taken, the dialog re-opens with the reason and a free name suggested (`… (2)`, `… (3)`). | `test_b2_duplicate_name_suggests_next` (node-run of the name function) ; `test_sal3_duplicate_name_is_409` (server, existing) |
| K1 | A failed summary shows its reason inside the popup, not only in the top bar. | `test_k1_summary_error_in_popup` |
| K2 | At startup, a key that is two identical halves, or has spaces/quotes, is reported in `/healthz` `startup_warnings` (shown as a banner) — the value is never logged. | `tests/web/test_llm_config.py::test_k2_*` |
| D1 | For a user with no saved default sources, the pickers start from the server's `default_selected` (bioRxiv, arXiv off). | `test_d1_*` (node-run `applyDefaultSources` with server defaults) ; `/healthz` carries `default_selected` |
| D2 | "Could not reach" names the reason class (e.g. "bioRxiv/medRxiv: the service returned an empty response"). | `tests/web/test_searches_routes.py::test_d2_*` |

**Your action (not code):** fix the doubled key in `.env` with the one-line
command from the chat (it keeps the first half only if both halves match), then
restart the server.

## Implementation order
1. C1, C2 (stale page), A1, B1, B2, K1 — small client fixes.
1b. K2, D1, D2.
2. S1 batch lookup route + tests.
3. S2 panel + result-row badges.
4. S3 search-results PDF route (reuses `src/summary_pdf.py`) + button.
5. Full suite; browser check of: stale-cache scenario (load old page, update,
   reload → new page without a hard refresh), save-all, two summaries both visible,
   PDF from search results.

## What you can do right now (before any code)
Hard-refresh the page: **⌘⇧R** in Chrome/Edge/Firefox, **⌥⌘R** in Safari. You
should then see "Saved Filters", a working "Save all N results…" button, and
"Export summaries (PDF)" on the Saved References tab.

## Adjacent issues found, not fixed
- Max results overshoots (asked for 12, got 40) — pre-existing; separate change.
- Pre-existing: any user can overwrite the shared summary for a DOI (TODO.md).
