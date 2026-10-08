# QA gate — clearer "results incomplete" summary + per-filter limit (2026-10-08)

RANGE:   origin/main..HEAD (3 commits)
COMMITS: 3ce2630 docs: plan WS1-WS7, FL1-FL6
         2e1f1e0 feat: clearer incomplete-results summary; a limit per saved filter (WS1-WS7, FL1-FL6)
         4be467b test: advance the retrieval-drift baseline to the WS/FL commit

Plan: docs/implementation_plan_2026-10-08_limits_and_warnings.md
Protected path touched: src/sources/orchestrator.py — one optional
on_source_limit callback (name, read, total, by_pages), nothing fetched,
merged or kept changes. Drift baseline advanced once (test_no_retrieval_drift.py).

## Check items

1. Summary advice cannot misfire — PASS
   - PubMed truncated only told "untick PubMed" when europepmc is active
     (limit_summary._action_key, line 66-67). test_ws4_pubmed_without_europe_pmc_is_not_told_to_untick.
   - OSF source with a Title word not told to add one; _osf_narrowed derives from the
     producer's own osf_title_terms (query_builder), not a hand-copy.
     test_ws4_osf_with_a_title_word_is_not_told_to_add_one,
     test_ws4_osf_group_without_title_anywhere_counts_as_not_narrowed.
   - total > ceiling never suggests raising (narrow_words instead; raise only when
     total <= ceiling). test_ws4_over_the_ceiling_never_suggests_raising (boundary 2000 fits).

2. Counts are honest — PASS
   - read = seen_raw (papers actually read from that source), total = src_total when the
     source reported one; "total not reported" otherwise. The documented PubMed 400-vs-200
     case (limit counts new papers; seen_raw counts all read) is preserved honestly.
   - "what was counted" follows source: matches for europepmc/pubmed/arxiv, "papers with
     your Title words" for a narrowed OSF source, "papers in the date range" otherwise.
     test_ws3_the_owners_case, test_ws3_total_not_reported.

3. Summary updates during a run — PASS
   - refresh_summary() is called from on_source_failure and on_source_limit, and again
     when the search ends. test_ws1_summary_shown_while_the_search_runs asserts the block
     is present while status is still "running".

4. Every saved-filter path uses the filter's limit; old page cannot override — PASS
   - Search Run (start_search): filter_id set → filter_limit(filter_dict), body.max_results
     ignored. test_fl2_saved_filter_uses_its_limit (sends max_results:50, sees 750).
   - Filters Test (test_filter): filter_limit(filter_dict). test_fl3_filter_test_uses_its_limit.
   - monitor: filter_limit per filter unless explicit --max (logged). test_fl4_*.
   - Frontend sends no max_results for a saved filter run. test_fl2_saved_filter_run_sends_no_limit.

5. Limit validation (bools, fractions, strings, range) — PASS
   - clean_limit rejects bool, fractions, non-numeric text, and out-of-range; accepts ints and
     whole-number strings; None/"" → default. test_fl1_bad_limit_refused_on_save parametrized
     [0, 2001, "lots", 1.5, True] → 400; test_fl1_limit_saved_and_read_back ("600" → 600, 2001 → 400);
     test_fl2_ad_hoc_uses_the_box (2001 → 400).

6. No innerHTML with data — PASS
   - renderLimitSummary builds everything with createElement/textContent. The WS5 fixture embeds
     an action "<b>Narrow</b> the words." and asserts it renders as literal text.
     test_ws5_summary_block; test_the_client_never_builds_markup_from_paper_data still green.

7. Edited older tests are justified — PASS
   - test_d2_failure_reason_from_config: "truncated" expectation renamed "raise Max results" →
     "raise the limit", matching the deliberate wording change in sources_config.yaml.
   - test_no_retrieval_drift.py: BASELINE 5eafea4 → 2e1f1e0, with a comment naming the WS2
     orchestrator callback as the reason — the drift baseline must follow the protected file.

## Full suite

venv/bin/python -m pytest tests/ -q  →  1831 passed, 2 skipped, 4 warnings
The 2 skips are pre-existing environment/CI skips (RLIMIT_AS Linux-only; Python-version check),
unrelated to this change. All WS/FL tests run and pass.

## Findings

F1 · P5 · low-medium · confidence high — bare int() on config values, asymmetric with the
    OSF adapter's defensive read. src/limit_summary.py:_osf_narrowed does
    `int(… .get("max_title_terms", 8))` while src/sources/osf.py:_max_title_terms wraps the
    same read in try/except → DEFAULT_MAX_TITLE_TERMS; src/search_limits.py:search_limits does
    the same bare int() on search.default_max_results / max_results_ceiling. A null or
    non-numeric value in those keys (blanking a value is a plausible config edit; the "missing
    key" warning only fires on absent keys, not null values) raises TypeError/ValueError. In the
    OSF path that propagates through limit_summary → refresh_summary → on_source_failure inside
    the search loop; the outer except re-reports "error", the handler's second refresh_summary
    raises again, and jobs.py _run marks the job ERROR (contained by the P15 guard, no hang).
    Net effect: a config typo turns every search into an ERROR job (and search_limits would also
    500 /api/config), instead of the graceful default the adapter already performs. The WS6/FL5
    tests cover missing keys, not null/non-numeric values. Fix: share the adapter's defensive
    read (or try/except), and add a null-value case to test_ws6 / test_fl5.
    Not blocking: requires a config edit outside this change's acceptance scope; current config
    is correct; failure is contained and recoverable; no data-correctness impact on valid config.

Notes (below-medium, backlog):
- _osf_narrowed hardcodes 8 as its fallback, duplicating osf.DEFAULT_MAX_TITLE_TERMS = 8 — the
  same single-source fix as F1 folds this in.
- limit_summary.WORD_SOURCES / OSF_SOURCES is a hand-maintained list that must mirror the
  orchestrator's six registered adapters; a future word-searching adapter not added here would
  be mislabeled "papers in the date range".

## Verdict

APPROVED — the seven stated acceptance criteria are met, each backed by a passing test, and
the full suite is green. One low-medium robustness finding (F1) is recorded for follow-up; it
is out of the gate's scope and does not affect the summary's correctness, count honesty, or
limit propagation on the current config.
