# QA gate — bioRxiv/medRxiv match budget + browser-run fixes (2026-09-29)

Review: learning-qa failure-pattern sweep over `origin/main...HEAD`.

RANGE:       origin/main...HEAD  (merge-base diff; origin/main = 63912ab)
COMMITS:     2 — f854662 "feat: bioRxiv/medRxiv count matches with a page limit; the other browser-run fixes"
                   2162447 "test(drift): advance the W1.a baseline for the bioRxiv match budget"
APPLICABLE:  P2, P3, P7, P9, P12, P13, P19, P21, P28, P29, P36
CHECKED:     P1 P2 P3 P4 P5 P7 P9 P12 P13 P19 P21 P28 P29 P36 (seed P1–P7 + project P8–P14 + generic P19/P28/P29/P36)
NOT COVERED: security beyond XSS sinks (P8 SSRF untouched), concurrency, performance,
             logic-correctness beyond the reviewed paths, authN/authZ.
SUITE:       venv/bin/python -m pytest tests/ -q  →  1605 passed, 2 skipped (green).

## What the change does (verified against source, not the changelog)

- `BiorxivMedrxivAdapter.filters_locally = True`; the orchestrator's `_search_source`
  applies `without_license(filter)` per record and counts only matches against
  `max_results`, while `_page_limit(source)` (config `max_pages`, default 150) bounds
  how much of the date window is read. A page-limit cut is a NEW failure kind,
  `page-limit`, with its own `failure_explanations` entry and status wording.
- Species "human"/"no-animal" now also drop a paper whose title names an animal study,
  via `animal_title_terms` in `filter_vocabulary.yaml` → `title_names_an_animal_study()`.
- Review Markdown parsed to blocks and rendered with createElement/textContent only.
- `markup_to_text` decodes HTML entities before removing tags; `has_markup` gates a
  startup migration that cleans stored titles/abstracts/abstract-only summaries.
- Source labels + "no key needed" wording; PubMed journal-or-server label; reference
  items carry `journal_or_server`.

## Checks requested — results

1. Orchestrator per-page filtering (budget, page limit, truncation, progress, P13):
   CORRECT, with one display defect (finding 2). Budget counts matches (verified by
   test_br3_biorxiv_budget_counts_matches: 4,900 read → 98 matches, no truncation).
   Page-limit is a distinct reported kind with its own message — P13 satisfied. Other
   sources are unchanged: `local_filter` is False for every adapter without
   `filters_locally`, and `_page_limit` falls back to the old MAX_PAGES_PER_SOURCE (20).

2. Other sources unaffected: CONFIRMED. Only biorxiv_medrxiv declares `filters_locally`;
   test_br3_other_sources_still_count_what_they_read asserts Europe PMC still counts 200.

3. Species title terms, false exclusions (P7): FAILS — see finding 1.

4. Markdown renderer never creates markup from model text: PASS. `renderMarkdownInto`
   uses only `document.createElement`, `node.textContent`, `createTextNode`; no innerHTML.
   `markdownBlocks`/`markdownRuns` emit data, not HTML. test_br4 asserts a literal
   `<script>` stays text. No P9 finding.

5. Migration touches only rows with known tags: PASS. The WHERE clauses select rows
   containing `<`/`&lt;`, then `has_markup` (same compiled tag regexes as
   `markup_to_text`, not a copy) gates the rewrite; `source_text='abstract'` only for
   summaries; full-text (model) summaries and `p < 0.05` text untouched (test_br8,
   idempotent on second open).

## FINDINGS (ranked)

F1 · P7 · src/filter_vocabulary.py:198 + filter_vocabulary.yaml `animal_title_terms` ·
    The species title check silently drops legitimate human studies whose titles name
    a reagent, device, cell line, anatomy, or statistical method rather than an animal
    study. Whole-word, case-insensitive matching makes organism words collide with
    non-study senses: "bovine serum albumin" (reagent), "porcine bioprosthetic valve"
    (device), "rabbit monoclonal antibody" (reagent), "Chinese hamster ovary cells"
    (cell line), "canine tooth" (anatomy), "MICE" = Multiple Imputation by Chained
    Equations (statistics), "equine-assisted therapy" (human therapy), "murine typhus"
    (human disease). Confirmed by probe: 8/8 such titles are dropped by
    `filter_papers(..., {"species":"no-animal"})`. The adversarial test
    test_br7_human_studies_that_look_close_are_kept covers only substring-embedding
    ("mousetrap") and pets ("dog"); the word-sense collision axis is untested. ·
    Fix: prune/contextualize the list (drop bovine/porcine/rabbit/hamster/canine/
    equine/murine, or require organism-model phrasing), and add these as must-keep
    adversarial cases. · confidence: high

F2 · P12/P36 (corollary "one progress channel, one quantity") ·
    src/sources/orchestrator.py:488 + search():233-241 · The cumulative progress wrapper
    sums quantities in different units: prior sources contribute `fetched` (matches),
    but a local-filter source reports `seen_raw` (papers read, up to ~4,900), so the
    progress bar's "fetched" is inflated ~17x during the slow bioRxiv phase and the
    total follows. The on_status lines are correct; only the on_progress callback is
    affected, and no test asserts on_progress for a local-filter source. ·
    Fix: report match-count through on_progress for local sources, or thread a separate
    "papers read" channel. · confidence: medium (low severity — display only).

## Notes (not findings)

- `has_markup` shares the module-level tag regexes with `markup_to_text` (correct
  single-source-of-truth); the only latent drift is a future 6th tag regex added to one
  but not the other. Minor.
- The migration runs a leading-wildcard LIKE scan on every DB open; O(N) per startup,
  negligible at preprints scale after the first clean.
- W1.a baseline advance (2162447) is the documented exception process; test-only.

## VERDICT: REJECTED

1 high-confidence finding (F1: silent false-exclusion of legitimate human studies by
the species title check), 1 low-severity display defect (F2). The markdown renderer,
migration scoping, page-limit reporting, and other-source isolation all pass. Fix F1
(a cheap term-list/adversarial-test change) before merging; F2 can go to the backlog.
