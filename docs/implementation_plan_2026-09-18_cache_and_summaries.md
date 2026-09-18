# Plan — stale page in the browser; summaries that accumulate and can be saved
**Date:** 2026-09-18
**Status:** awaiting approval — no code written

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
| L1 | Label on the Search & Browse tab stays **"Saved Filters"** (the desktop name, per 2026-09-18). *Confirm: did you mean "Saved Filters" (already served) or literally "Save Filters"?* | existing `test_rn1_labels_match_the_desktop` |
| S1 | Each result row shows whether the paper has a stored summary ("✓ Summary"), found with one batch lookup per results page, not one request per paper. | `tests/web/test_summaries_routes.py::test_s1_batch_lookup_*` (new `POST /api/summaries/lookup-batch`, max 200 papers, per-paper result) |
| S2 | A **Summaries** panel on Search & Browse lists every summary for the current results, newest first; a new summary is added to the panel, never replacing another. Clicking one opens the popup. | `test_frontend_wiring.py::test_s2_*` (node-run of the panel's add/merge function: adding B keeps A) ; browser check |
| S3 | **"Save summaries (PDF)"** on Search & Browse exports the summaries for the current results (ticked papers if any are ticked, else all), same layout as the Saved References export. | `tests/web/test_summaries_pdf.py::test_s3_*` (new `GET /api/searches/{job_id}/summaries.pdf`, owner-only, only a finished search) |
| S4 | The Saved References export is unchanged and still available. | existing `test_sp1_*`–`test_sp5_*` |

## Implementation order
1. C1, C2 (fixes 1, 2, 4 for everyone, now and on every later update).
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
