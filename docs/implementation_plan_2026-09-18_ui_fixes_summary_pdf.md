# Plan — web UI fixes, desktop naming, summaries-to-PDF export
**Date:** 2026-09-18
**Status:** implemented 2026-09-18 (777 passed). Approved in chat (2026-09-18): fixes + desktop names; PDF scope = one
Saved References list; papers without a summary are listed as not summarized.

## Problem (from Dave's first real use of the web app)

- Detail popup and summaries render ~2,900 px down the page (no overlay CSS;
  summary card sits below the results table), so they look like they do nothing.
- "Save as reference list" is disabled until a paper is ticked, with no hint, and
  its confirmation appears at the top of the page, off-screen.
- Labels differ from the desktop app, which hides the model:
  Saved Filter → Run → search results (temporary) → save → Saved References.

## Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| UI1 | The paper detail popup is a fixed overlay in the viewport; closes on ✕, Esc and backdrop click. | `test_frontend_wiring.py::test_ui1_modal_is_a_fixed_overlay` (CSS rule for `.modal-overlay` with `position: fixed`); browser check |
| UI2 | Summarize opens the detail popup and shows progress, then the summary, inside it. The below-the-table summary card is removed. | `test_ui2_summary_renders_in_the_modal` (startSummary opens the modal; summary ids live inside `#paper-modal`); browser check |
| UI3 | The message bar stays visible while scrolled. | `test_ui3_notice_is_sticky` (CSS `#notice` sticky/fixed); browser check |
| UI4 | The save button reads "Save selected (N)" or "Save all N results" and is enabled whenever there are results; with nothing ticked it saves all. | `test_ui4_save_button_label` (node-run label function); `test_filter_test_route.py` save-all path already covered |
| RN1 | Web labels match the desktop: tabs "Search & Browse", "Filters", "Saved References", "Settings"; the Search tab's filter list is "Saved Filters". | `test_rn1_labels_match_the_desktop` (reads `gui.py` tab labels and `index.html`) |
| PF1 | The save-list name is pre-filled: "<filter name> – <date>" for a filter run, "<search words> – <date>" for a manual search. | `test_pf1_default_list_name` (node-run) |
| SP1 | `GET /api/references/{id}/summaries.pdf` returns a PDF; another user's list is 404. | `tests/web/test_summaries_pdf.py::test_sp1_*` |
| SP2 | Each paper appears with title, authors, date, DOI; a summarized paper shows key findings, methodology, conclusions and the model that wrote it. | `test_sp2_*` (text extracted from the PDF with pdfplumber) |
| SP3 | A paper with no summary appears as "Not summarized"; the first page states "N of M papers summarized". Nothing is invented. | `test_sp3_*` |
| SP4 | Non-Latin text (Greek letters, accents, dashes) renders when a Unicode font is available; if none is, unsupported characters are replaced and the count is stated on the first page. | `test_sp4_*` |
| SP5 | Font path overridable with `BIORX_PDF_FONT`; the Docker image installs `fonts-dejavu-core`. | `test_sp5_*`; `test_deploy_files.py` |
| SP6 | References tab has "Export summaries (PDF)" next to Export CSV. | `test_frontend_wiring.py` endpoint enumeration + element ids |

## Not code-testable
- Visual layout of the popup and PDF: browser check and opening a generated PDF.

## Deferred (offered, not asked for)
- Recording which filter produced a Saved References list (needs a schema column).

## Status

| ID | Status | Proof |
|---|---|---|
| UI1 | done | `test_frontend_wiring.py::test_ui1_modal_is_a_fixed_overlay`; browser: popup box at top 38 px of the viewport, Esc closes |
| UI2 | done | `test_ui2_summary_renders_in_the_modal`; browser: stored summary and a failed run both shown in the popup |
| UI3 | done | `test_ui3_notice_is_sticky`; browser: notice at 8 px from the top while scrolled |
| UI4 | done | `test_ui4_save_button_label` (3 cases), `test_ui4_nothing_ticked_saves_all`; browser: "Save all 40 results", saved 40 |
| RN1 | done | `test_rn1_labels_match_the_desktop` (reads gui.py) |
| PF1 | done | `test_pf1_default_list_name` (3 cases); browser: "cortisol, maternal – 2026-09-18" |
| SP1–SP5 | done | `tests/web/test_summaries_pdf.py` (10 tests, text read back with pdfplumber) |
| SP6 | done | `test_sp6_references_tab_has_the_pdf_export`; browser: 200, application/pdf |
| Live summary via a real model | not verified | No model on the test server; the popup was checked with one fixture summary in a throwaway database and with a failed run |
