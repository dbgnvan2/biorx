# Implementation plan — summaries from full text, and finding free copies

**Request:** chat, 2026-09-19.
**Status:** IMPLEMENTED 2026-09-19 (b73bcdf, 30bfb3b, 98c107c, 5597e5c). C1 revised by the owner: abstract stands in, no button.

## 0. Spec IDs

| ID | Verbatim request |
|---|---|
| FT1 | "I getting summarizes based on an abstract only - which is a waste of tokens AND misleading." |
| FT2 | "would google scholar or research gate be good to add." |
| FT3 | "often a search of the title will find a PDF copy vs. just going to the publisher. Can this be an option." |

## 1. Findings

- `web/routes_summaries.py:_run_summary` summarizes whenever there is an abstract,
  full text or not. `_extract_text` records why full text was not used
  (`outcome["full_text"]`), which the modal shows in small print
  (`web/static/app.js:1152`). It is not stored with the summary, so the ✓ badge,
  the list, and the summaries PDF cannot say "abstract only".
- Until 2026-09-19 `BIORX_CONTACT_EMAIL` had not reached the Railway service, so
  Unpaywall — the main source of `pdf_url` — was off and most papers had no PDF
  link. It is set now; papers with no Unpaywall copy still fall back silently.
- PDF text comes only from `paper_meta.pdf_url()`: enrichment's `pdf_url`/`best_oa_url`,
  a bioRxiv pattern, or the DOI resolver (a publisher page, usually not a PDF).
- An OpenAlex adapter exists but is disabled as a search source.

## 2. Changes

**C1 (FT1) — no model call without full text; the abstract stands in.**
(Revised 2026-09-19 by the owner: no "summarize anyway" button.)
- When no full text is found (after the C2 chain), the model is not called and no
  owner-key usage is recorded. The abstract itself is stored as the entry, with
  `source_text = abstract`, `model_version = ""`, and shown as
  "Abstract — no full text found (not a model summary)".
- A full-text summary stores `source_text = full_text` and `text_source` (where the
  text came from). Existing rows have `source_text = ''` (unknown).
- Badge: **✓ Summary** for full text, **✓ Abstract only** for the stand-in; the
  details view and the summaries PDF say the same.
- Summarize on a paper whose stored entry is abstract-only runs the full-text
  search again (a copy may have appeared) rather than returning the stand-in.
- No abstract and no full text: the existing recovery chain, then an error.
- Desktop/CLI summarizer: same rule.

**C2 (FT3) — find a free full-text copy before giving up.** A chain in
`src/fulltext.py`, tried in order, stopping at the first PDF that downloads and
yields text:
1. the paper's own `pdf_url` / `best_oa_url` (as now);
2. Unpaywall by DOI (needs `BIORX_CONTACT_EMAIL`);
3. OpenAlex by DOI, else by title — every open-access location, including
   repository and author copies;
4. Semantic Scholar by DOI, else by title — `openAccessPdf`.
   (CORE dropped 2026-09-19: it needs an API key the owner cannot get.)
- **Title matching is strict:** normalised titles equal (case, punctuation,
  whitespace) AND (first-author surname matches OR year matches). No fuzzy
  "close enough". A near-miss is rejected and logged.
- Every download goes through `src/safe_fetch` (the existing SSRF guard), with
  timeout + retry + backoff on each new HTTP call (P5).
- Option: `find_full_text_by_title` in `sources_config.yaml` (default on) plus a
  per-user toggle in Settings ("Look for free copies by title"). DOI lookups
  always run; the toggle only governs title searches.
- Where the text came from is stored with the summary ("full text via OpenAlex").

**C3 (FT2) — Google Scholar / ResearchGate: not added.** No public API; both
forbid automated access; Scholar blocks scripts with CAPTCHAs. Recorded in the
README. OpenAlex and Semantic Scholar (C2) cover the same need, keyless.

**C4 — Railway.** `BIORX_CONTACT_EMAIL` is set (confirmed 2026-09-19, Unpaywall on).
`/healthz` reports which finders are active.

## 3. Acceptance criteria → tests

| ID | Criterion | Test |
|---|---|---|
| FT1.1 | No full text → no model call, no usage; abstract stored with `source_text=abstract` | `tests/web/test_summaries_routes.py::test_ft1_1_no_full_text_stores_the_abstract_without_a_model_call` |
| FT1.2 | Summarize on an abstract-only entry searches again; a found copy replaces it | `…::test_ft1_2_abstract_only_entry_is_retried` |
| FT1.3 | Full text found → stored `source_text=full_text` + where from | `…::test_ft1_3_full_text_source_is_recorded` |
| FT1.4 | Badge, details view and summaries PDF say "abstract only" | `tests/web/test_frontend_wiring.py::test_ft1_4_abstract_only_is_labelled` (node), `tests/web/test_summaries_pdf.py::test_ft1_4_pdf_says_abstract_only` |
| FT1.5 | Desktop/CLI: no model call without full text; abstract stored as stand-in | `tests/test_summarization_agent.py::test_ft1_5_agent_uses_the_abstract_without_a_model_call` |
| FT3.1 | Chain order; stops at the first source with extractable text | `tests/test_fulltext.py::test_ft3_1_chain_order_and_stop` |
| FT3.2 | Adversarial: similar title, different paper → rejected | `tests/test_fulltext.py::test_ft3_2_near_miss_title_is_rejected` |
| FT3.3 | Same title, wrong author and year → rejected | `tests/test_fulltext.py::test_ft3_3_title_alone_is_not_enough` |
| FT3.4 | Title search off → only DOI lookups run | `tests/test_fulltext.py::test_ft3_4_title_search_can_be_turned_off` |
| FT3.6 | A source timing out is retried, then reported as unreachable — not "no copy" | `tests/test_fulltext.py::test_ft3_6_outage_is_not_absence` |
| FT3.7 | Downloads go through the SSRF guard | `tests/web/test_summary_fetch_guard.py::test_ft3_7_finder_downloads_are_guarded` |
| C4 | `/healthz` lists active finders | `tests/web/test_app.py::test_c4_healthz_lists_full_text_finders` |

Integration-only (flagged, not claimed): live calls to OpenAlex and Semantic Scholar. Proposal: after deploy, run one known paper through each and note which
source found it.

## 4. Order

1. C1 (stops the waste immediately), with FT1 tests.
2. C2 finder chain + FT3 tests (adversarial title tests first).
3. Settings toggle, healthz, README/Railway notes.
4. Gate (/chdp) before push.
