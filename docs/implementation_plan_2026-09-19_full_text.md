# Implementation plan — summaries from full text, and finding free copies

**Request:** chat, 2026-09-19.
**Status:** DRAFT — awaiting approval. No code changed.

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
- On Railway `BIORX_CONTACT_EMAIL` is unset, so Unpaywall — the main source of
  `pdf_url` — is off (`/healthz` warning). Most papers therefore have no PDF link.
- PDF text comes only from `paper_meta.pdf_url()`: enrichment's `pdf_url`/`best_oa_url`,
  a bioRxiv pattern, or the DOI resolver (a publisher page, usually not a PDF).
- An OpenAlex adapter exists but is disabled as a search source.

## 2. Changes

**C1 (FT1) — no silent abstract-only summaries.**
- When no full text is found, stop before calling the model. The job ends with
  status `needs_confirmation`, listing where it looked and why each failed.
- The details view shows that, with a **Summarize from the abstract anyway** button
  (sends `allow_abstract_only: true`). No model call, no cost, without the click.
- Every stored summary records `source_text` = `full_text` | `abstract` (new column;
  existing rows = `unknown`). Shown as "Based on: full text / abstract only" in the
  details view, a distinct badge (**✓ Summary (abstract)**), and the summaries PDF.
- Desktop/CLI summarizer: same rule; the CLI takes `--allow-abstract-only`.

**C2 (FT3) — find a free full-text copy before giving up.** A chain in
`src/fulltext.py`, tried in order, stopping at the first PDF that downloads and
yields text:
1. the paper's own `pdf_url` / `best_oa_url` (as now);
2. Unpaywall by DOI (needs `BIORX_CONTACT_EMAIL`);
3. OpenAlex by DOI, else by title — every open-access location, including
   repository and author copies;
4. Semantic Scholar by DOI, else by title — `openAccessPdf`;
5. CORE by DOI, else by title — needs `CORE_API_KEY` (free); skipped, and said so,
   without it.
- **Title matching is strict:** normalised titles equal (case, punctuation,
  whitespace) AND (first-author surname matches OR year matches). No fuzzy
  "close enough". A near-miss is rejected and logged.
- Every download goes through `src/safe_fetch` (the existing SSRF guard), with
  timeout + retry + backoff on each new HTTP call (P5).
- Option: `find_full_text_by_title` in `sources_config.yaml` (default on) plus a
  per-user toggle in Settings ("Look for free copies by title"). DOI lookups
  always run; the toggle only governs title searches.
- Where the text came from is stored with the summary ("full text via CORE").

**C3 (FT2) — Google Scholar / ResearchGate: not added.** No public API; both
forbid automated access; Scholar blocks scripts with CAPTCHAs. Recorded in the
README. OpenAlex, Semantic Scholar and CORE (C2) cover the same need.

**C4 — Railway.** Set `BIORX_CONTACT_EMAIL` (turns Unpaywall on) and optionally
`CORE_API_KEY`. `/healthz` reports which finders are active.

## 3. Acceptance criteria → tests

| ID | Criterion | Test |
|---|---|---|
| FT1.1 | No full text → no model call; job ends `needs_confirmation` with reasons | `tests/web/test_summaries_routes.py::test_ft1_1_no_full_text_does_not_call_the_model` |
| FT1.2 | `allow_abstract_only` → model called; stored `source_text=abstract` | `…::test_ft1_2_abstract_only_needs_consent_and_is_recorded` |
| FT1.3 | Full text found → stored `source_text=full_text` + where from | `…::test_ft1_3_full_text_source_is_recorded` |
| FT1.4 | Badge, details view and summaries PDF say "abstract only" | `tests/web/test_frontend_wiring.py::test_ft1_4_abstract_only_is_labelled` (node), `tests/web/test_summaries_pdf.py::test_ft1_4_pdf_says_abstract_only` |
| FT1.5 | Desktop/CLI refuse abstract-only without the flag | `tests/test_summarization_agent.py::test_ft1_5_agent_needs_consent_for_abstract_only` |
| FT3.1 | Chain order; stops at the first source with extractable text | `tests/test_fulltext.py::test_ft3_1_chain_order_and_stop` |
| FT3.2 | Adversarial: similar title, different paper → rejected | `tests/test_fulltext.py::test_ft3_2_near_miss_title_is_rejected` |
| FT3.3 | Same title, wrong author and year → rejected | `tests/test_fulltext.py::test_ft3_3_title_alone_is_not_enough` |
| FT3.4 | Title search off → only DOI lookups run | `tests/test_fulltext.py::test_ft3_4_title_search_can_be_turned_off` |
| FT3.5 | No `CORE_API_KEY` → CORE skipped and reported, others run | `tests/test_fulltext.py::test_ft3_5_core_without_key_is_skipped_and_said` |
| FT3.6 | A source timing out is retried, then reported as unreachable — not "no copy" | `tests/test_fulltext.py::test_ft3_6_outage_is_not_absence` |
| FT3.7 | Downloads go through the SSRF guard | `tests/web/test_summary_fetch_guard.py::test_ft3_7_finder_downloads_are_guarded` |
| C4 | `/healthz` lists active finders | `tests/web/test_app.py::test_c4_healthz_lists_full_text_finders` |

Integration-only (flagged, not claimed): live calls to OpenAlex, Semantic Scholar
and CORE. Proposal: after deploy, run one known paper through each and note which
source found it.

## 4. Order

1. C1 (stops the waste immediately), with FT1 tests.
2. C2 finder chain + FT3 tests (adversarial title tests first).
3. Settings toggle, healthz, README/Railway notes.
4. Gate (/chdp) before push.
