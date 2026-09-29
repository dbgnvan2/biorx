# QA gate — production check of the bioRxiv search, and its two fixes (2026-09-29)

Review: learning-qa failure-pattern sweep over `origin/main...HEAD`.

RANGE:       origin/main...HEAD  (merge-base diff; origin/main = 54a98c2)
COMMITS:     4 — 21d2b35 "fix: account for every bioRxiv paper read; tables scroll inside their card"
                 a800897 "test(drift): advance the W1.a baseline for the bioRxiv paper accounting"
                 28d2963 "docs: production check of the bioRxiv search, and its two fixes"
                 2d4f934 "docs: changelog wording"
APPLICABLE:  P2, P5, P12, P19, P28, P36
CHECKED:     P1 P2 P5 P7 P9 P12 P19 P28 P36 (seed P1–P7 + project P8–P14 + generic P19/P28/P36),
             plus the sweep's own pitfall class: derived counts mirroring a producer must share
             the producer's real code, not a parallel hand-copy.
NOT COVERED: security beyond XSS sinks (P8 SSRF untouched), concurrency, performance,
             logic-correctness beyond the reviewed paths, authN/authZ.
SUITE:       venv/bin/python -m pytest tests/ -q  →  1612 passed, 2 skipped (green).

## What the change does (verified against source, not the changelog)

- `SourceOrchestrator._search_source` now counts `duplicates` — records that pass the
  local filter but merge into a paper already read (another version of the same DOI).
  The final log line says read = match + do-not-match + repeats + unreadable, and warns
  when `fetched + not_matching + duplicates + unreadable != seen_raw`. The status line
  gains "(N more were repeats of a paper already read)" when N > 0.
- Every `<table>` in `web/static/index.html` (3 tables: results, filter test, references)
  is wrapped in `<div class="table-scroll">`; `styles.css` adds
  `.table-scroll { overflow-x: auto; max-width: 100% }`.
- `tests/test_adapters.py::_api_with_pages` gains a `doi=` injection so a test can make
  records share a DOI; `tests/web/test_no_retrieval_drift.py` advances the W1.a baseline
  7acd226 → 21d2b35, a documented W1.a exception.

## Checks requested — results

1. Reconciliation holds on every path; the warning cannot fire falsely: CORRECT.

   The reconciliation and warning live inside `if local_filter:` (orchestrator.py:510), and
   only `biorxiv_medrxiv` declares `filters_locally = True` (grep across src/sources confirms
   no other adapter sets it). For that one source the identity `accounted == seen_raw` is
   provable, path by path:

   - Every raw record is counted exactly once: the per-record loop dispatches each record to
     exactly one of `unreadable` (normalize raised), `not_matching` (filter said no),
     `duplicates` (dedup.add returned no growth), or `fetched` (new match).
   - `seen_raw` accumulates `page_size_seen`, and for bioRxiv
     `last_page_size == len(raw_records)` always: the adapter sets
     `self.last_page_size = returned` where `returned == len(out)` (biorxiv_medrxiv.py:118–123).
     So each page adds exactly as many to `seen_raw` as records are processed.
   - Whole-page-filtered pages (`if not raw_records: … continue`, orchestrator.py:444): for
     bioRxiv an empty page implies `last_page_size == 0`, which hits `if not page_size_seen:
     break` (line 431) *before* `seen_raw` is incremented — so this branch never runs for the
     one source the reconciliation covers. (It exists for arXiv's withdrawn-paper filtering,
     which is NOT `filters_locally`, so the reconciliation never runs for it.)
   - Unreadable records: counted via the `+ unreadable` term; the pre-existing
     "N of M could not be read" block is orthogonal and still correct.
   - Truncation: `max_results` / page-cap breaks happen at the top of the *next* iteration,
     after the current page is fully counted, so `seen_raw` only ever holds fully-processed
     pages. No partial-page inflation.
   - Non-local-filter sources: the `if local_filter:` guard means neither the log line nor the
     warning can fire for Europe PMC, PubMed, PsyArXiv, SocArXiv, arXiv, or Crossref.

   Conclusion: the warning is a real invariant check and cannot fire falsely for any source.

   Regression test `tests/test_orchestrator.py::test_br10_every_paper_read_is_accounted_for`
   drives the *real* `BiorxivMedrxivAdapter` (API monkeypatched), asserts the exact accounting
   300 = 21 match + 270 do-not + 9 repeat + 0 unreadable, the exact status suffix, and that
   "accounted for" never appears in the log. Exact `==`-style values, not floors.

2. No JS depends on the table's parent element: CONFIRMED.

   `web/static/app.js` builds rows with `document.createElement("tr")` and appends them to
   tbodies fetched by id (`results-body`, `filter-test-body`, `ref-papers-body`) via
   `getElementById`. No `parentNode`, `.parentElement`, `closest(`, `previousElementSibling`
   or `nextElementSibling` anywhere in app.js, and no `querySelector` path that traverses
   from a table/row to its wrapper. The `.table-scroll` div is transparent to the JS.
   CSS has no descendant/combinator rule tying `table` to `.card` (only `table { width: 100% }`,
   `tbody tr:hover`), so the new wrapper breaks no layout selector.

   Regression test `tests/web/test_frontend_wiring.py::test_br11_every_table_scrolls_inside_its_card`
   asserts all 3 `<table>` occurrences are preceded by `<div class="table-scroll">` and that the
   `.table-scroll` rule carries `overflow-x: auto`. Verified independently: 3 `<table`, 3 wrappers.

3. W1.a baseline advance: LEGITIMATE. BASELINE = 21d2b35 (the tip of the fix commit), and the
   three later commits (a800897, 28d2963, 2d4f934) touch only tests, docs, and CHANGELOG — none
   of the 14 PROTECTED files — so `git diff 21d2b35..HEAD -- PROTECTED` is empty and the guard
   stays meaningful (`test_the_guard_would_notice_a_change` proves the filter works). The advance
   is documented inline and in the commit message per the W1.a exception process.

## FINDINGS (ranked)

None.

## VERDICT: APPROVED

Both production fixes are correct and adversarially bounded. The reconciliation invariant
(`fetched + not_matching + duplicates + unreadable == seen_raw`) is provably sound on every
path for the single local-filter source, and is structurally unable to fire for any other
source; the table-scroll wrapper is opaque to the JS, which reaches each tbody by id and never
by structural parent. Each fix carries an exact-value regression test; the W1.a baseline advance
is a documented exception, not a silent widening. Full suite green: 1612 passed, 2 skipped.
