# QA Gate — Retrieval correctness (review batch 1) + empty Ad Hoc Search box

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (4 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1418 passed, 1 skipped** (58.19 s; 4 warnings, all Starlette deprecation notices)

RANGE:       origin/main..HEAD (caller-supplied). origin/main is 0 behind, so the
             two-dot diff equals the merge-base diff. Materialized to /tmp/sweep.diff
             (5,101 lines) and audited hunk-by-hunk, then read the changed files in
             context (orchestrator.py, filtering.py, query_builder.py, routes_searches.py,
             routes_filters.py, biorxiv_api.py, config.py).
COMMITS:     4 commits:
             426fc49 test(drift): move the retrieval baseline past review batch 1
             493288a fix(retrieval): review batch 1 — every source searched, filters mean what they say
             6cee6f9 fix(search): refuse an empty Ad Hoc Search box before sending it
             0b8119d docs: full-repo review and the plan to fix its findings
APPLICABLE:  P1 (transient failure as permanent negative — M21 outage→raise), P2
             (silent drop — B1 truncation, M19/M20 unreadable/enrich errors now loud),
             P3 (narrow-scope exclusion — B1 budget, B3 OSF terms, B4 wildcard, B5
             facets, B7 medRxiv), P5 (sibling robustness — M21 retry, for_search() on
             every adapter), P6 (trusted derived field — version/last_total/last_page_size),
             P19 (producer/consumer drift — FAILURE_STATUS_MARKER wording, source labels),
             P28 (test→real-artifact isolation — arXiv module-global spacing), P29 (floor
             assertion — all new tests assert exact values).
CHECKED:     P1, P2, P3, P5, P6, P19, P28, P29. Read every changed source file, traced
             the web search flow (pre-enrichment filter → enrich → full filter), the
             monitor flow (enrich_only with without_license), the orchestrator paging
             loop (seen_raw/last_page_full/src_total truncation detection), and verified
             `BioRxivAPI.search_by_date_range(start_date, end_date, category, server,
             cursor)` exists with the exact signature the adapter calls (the tests mock
             it, so I checked the real class). Confirmed every plan-ID (B1-B7, S3,
             M18-M22, M31) has a same-named test, and that new tests assert exact
             values (== counts, exact list equality, exact status strings), not floors.
             Ran the full suite green.
NOT COVERED: the docs commit's claims (REVIEW-biorx-2026-09-28.md and the plan) are
             prose, not code — the findings it lists are Batch-1 IDs only. The review's
             remaining IDs (A1-A14, B8-B10, M1-M17, M23-M39, S1, S2, S4, T1, T2) are
             Batches 2-8 and are deliberately NOT in this range. Untracked files
             (`.claude/`, `.test-qa-report.md`) are outside the committed range.
             `gui.py` and its tests are not touched by this diff (recorded, not fixed,
             per the plan). Live provider behaviour (how OSF/arXiv/bioRxiv actually
             answer the new params) is only covered by fixtures, not exercised live.

---

## The change is Batch 1 of the implementation plan, and it delivers

Every retrieval finding in the plan is addressed and pinned by a test that carries
the finding ID. The master principle — make failure loud, make negatives provable —
is visibly applied: truncation and unreadable-record counts now emit
`FAILURE_STATUS_MARKER` status lines, enrichment program errors are counted
separately from outages (M19), and outage-vs-not-found is distinguished by raising
instead of returning `""`/`None` (M21, P1).

Specific hazards the batch existed to close, each verified in context:

- **B1 per-source budget.** `orchestrator._search_source` sets `budget = max_results`
  per source and drops the shared-budget `break`. Truncation detection is
  `more_left = (seen_raw < src_total) if src_total else last_page_full`, gated on
  `limited`, so an exact fit is not reported truncated
  (`test_b1_exact_fit_is_not_truncation`) and a page-cap cut is
  (`test_b1_page_cap_truncation_is_reported`). Correct.
- **B6 per-search adapters.** `_adapter_for_search` calls `for_search()` only when the
  class (not the instance) defines it, so real adapters hand out fresh instances and
  `MagicMock` test doubles pass through unchanged. arXiv spacing is a module-global
  lock; the conftest autouse fixture `_fresh_arxiv_spacing` saves/restores
  `_last_request_time` so tests are isolated (P28 handled, not just noted).
- **B5 licence-after-enrichment.** The web path filters with `without_license(filter)`
  to decide `matched_ids`, enriches exactly those, then re-runs the full filter. I
  traced `on_batch` → `matched_ids` → `enrich_only=lambda r: id(r) in matched_ids` →
  `_enrich` runs after all batches → final `filter_papers(matched, filter_dict)`. The
  object-identity gating is sound (dedup preserves identity on merge). Correct.
- **B7/M18 bioRxiv+medRxiv.** The adapter now uses `get_date_range` +
  `search_by_date_range`, queries both servers, and carries `version`. Verified the
  real `BioRxivAPI.search_by_date_range` signature matches the call site.
- **B2 empty dates.** `get_date_range` uses `or` so `""`/`None` dates fall back to the
  `days_back` window for arXiv and OSF; Europe PMC's `_date_clause` already used
  `if not`, so it was unaffected — consistent with the review.

## Findings (ranked)

None at medium or higher confidence. The following are non-blocking; recorded so they
are not silently dropped:

1. **Per-record re-normalization in the enrich hot path (perf, low).**
   `agents/monitor.py` and `web/routes_searches.py:172` call `filter_papers([...])`
   once per DOI-bearing record via `enrich_only`, and `filter_papers` runs
   `normalise_filter(f)` on every call (`src/filtering.py:188`), re-walking all six
   facets through `normalise_value`/`option_ids` for an already-normalized filter.
   Correct but wasteful (O(records × facets)); `load()` is `lru_cache`d but the option
   lists are rebuilt each time. The review already scheduled the N+1/perf sweep for a
   later batch (M11); worth folding this in. Not a gate block.

2. **Two source-label maps now coexist (P19, low).** `src/sources/orchestrator.py:25`
   imports `SOURCE_LABELS` from config.py for the new M31 warning (`:127`), while the
   pre-existing private `_SOURCE_LABELS` (`:36`) still feeds `_source_label()` for all
   status lines. If the two maps drift, a warning and a status line will name a source
   differently. Consolidation into a public `source_label()` is a Batch-3 (S2) item,
   so this is acknowledged scope — but the import introduces the second map one batch
   early. No drift exists today (both carry the same 8 entries).

3. **Multi-word surnames still don't dedup across formats (P19/M22 residual, low, pre-existing).**
   `src/sources/dedup.py::_surname` normalizes the given surname, but Europe PMC's
   structured `"da Silva"` becomes `dasilva` while arXiv's last-word fallback for
   `"Tiago da Silva"` becomes `silva`. The diff fixed the concrete M22 case
   (`"Smith J"` → `j`) but did not close the general multi-word-surname case, which
   the review's M22 note flagged as an open question. Not introduced by this diff.

---

## Verdict: APPROVED

Clean against P1-P29 of which P1, P2, P3, P5, P6, P19, P28, P29 were applicable.
Every Batch-1 plan ID (B1-B7, S3, M18-M22, M31) is implemented and pinned by a
same-named test asserting exact values, the two blocker-class hazards (per-source
budget starvation, shared pagination state) are closed and tested, and the full suite
is green (1418 passed, 1 skipped). Findings 1-3 are non-blocking: 1 is a perf note
for the scheduled perf batch, 2 is the acknowledged S2 consolidation pulled one batch
early, and 3 is a pre-existing residual of the M22 fix. None reopen the gate.
