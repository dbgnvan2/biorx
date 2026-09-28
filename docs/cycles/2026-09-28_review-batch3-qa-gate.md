# QA Gate — Filters and run preconditions (review batch 3)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (2 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — one COLD pass (a fresh subagent
  given only the diff, the range, and the three catalogue files, with no knowledge
  of how the code was written or of prior passes), plus independent verification
  by the gate author.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1473 passed, 1 skipped**
  (63.55 s; 4 warnings, all Starlette deprecation notices) — up 9 from batch 2's
  1464.

RANGE:       origin/main..HEAD (caller-supplied; rule 1 of the review-range order).
             origin/main is 0 behind, so the two-dot diff equals the merge-base
             diff. Materialized to /tmp/sweep.diff (1,823 lines) and read
             hunk-by-hunk; changed files then read in full context.
COMMITS:     2 commits:
             528ac0c test(drift): move the retrieval baseline past review batch 3
             dd0f496 fix(filters): review batch 3 — no silent overwrites, one place for run rules
APPLICABLE:  P2 (silent drop — seed path still overwrites; S4 removal completeness),
             P3 (narrow-scope exclusion — case folding, doc coverage), P10 (run
             precondition in shared code, not per front end — the S2 EmptyFilterError),
             P19 (producer/consumer drift — on_source_failure vs status-text parsing),
             P22 (return-contract change — upsert_filter split, failure_reason kind
             now a string not a message), P31 (absence-proof over a narrowed
             population — the S4 "no reference remains" scan).
CHECKED:     P2, P3, P10, P19, P22, P31. Read every changed source file
             (orchestrator.py, config.py, user_store.py, routes_filters.py,
             routes_searches.py, routes_discover.py, app.py, monitor.py, run.sh)
             and the changed tests. Traced the three entry points (web route,
             monitor CLI, gui.py) that now hit the engine's EmptyFilterError, the
             source-label consolidation (config.SOURCE_LABELS + source_label()
             replacing the deleted orchestrator._SOURCE_LABELS), and the
             insert/update/upsert split in user_store. Ran the full suite green.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness, concurrency (incl. the filter_name_taken check-then-insert
             TOCTOU and the case-sensitive UNIQUE index's interaction with it),
             auth/authz, injection & security, performance, dependency risk,
             API-contract compatibility, test quality, architecture. Live provider
             behaviour is exercised by fixtures, not live APIs. gui.py reviewed at
             its two orchestrator.search call sites, not end-to-end.

---

## The change is Batch 3 of the plan, and it delivers

Every plan ID (A2, S2, B13, M2, S4) is implemented and pinned by a same-named test
asserting exact values. The two themes — "no silent overwrites" and "one place for
run preconditions" — are both visibly applied.

- **A2 (no silent overwrite on save).** `web/routes_filters.py` now refuses a name
  clash with 409 before create/update, and `src/user_store.py` gained
  `insert_filter`/`update_filter`/`filter_name_taken` so a rename updates the row
  **in place** instead of upserting on the new name and deleting the old row (the
  mechanism that let a rename onto another filter's name overwrite it). Pinned by
  `test_a2_rename_keeps_the_id`, `test_a2_rename_onto_existing_is_409` (asserts both
  filters unchanged after the refused rename), `test_a2_create_duplicate_is_409`,
  `test_a2_case_only_rename_of_self_ok`.
- **S2 / B13 (preconditions and failures in the engine).** `orchestrator.search`
  now normalises the filter and raises `EmptyFilterError` on an empty one, so the
  empty-filter guard no longer lives per front end (learnings P10). Source failures
  arrive through a structured `on_source_failure(source, kind)` callback; web and
  monitor stop parsing status text. The two label maps are consolidated into one
  `config.SOURCE_LABELS` + public `source_label()` (the batch-1 gate's finding 2,
  resolved). Pinned by `test_s2_empty_filter_refused_in_engine`,
  `test_s2_legacy_filter_normalised_before_querying`, `test_s2_failures_structured_not_parsed`,
  `test_b13_legacy_filter_normalised`, `test_s2_web_records_failures_from_the_real_orchestrator`.
- **M2 (discover guarded and not enriched).** `routes_discover.discover_terms`
  refuses a whitespace description with 400 before a slot is reserved, and
  `_run_discover` passes `enrich_only=lambda _r: False` so the two HTTP calls per
  paper are not spent on a run that only reads titles/abstracts. Pinned by
  `test_m2_whitespace_description_400` (asserts 400 and that the orchestrator was
  never called) and `test_m2_no_enrichment`.
- **S4 (one search engine).** `agents/search_agent.py` and `key_terms.json` are
  deleted, `run.sh search` now execs `monitor.py --all`, and README/QUICKSTART/
  CLAUDE.md no longer describe the legacy agent. Pinned by
  `test_s4_legacy_agent_removed` and `test_s4_run_sh_search_uses_monitor` (the
  latter also `bash -n`-checks run.sh).

Specific hazards the batch existed to close, verified in context:

- **EmptyFilterError cannot escape uncaught.** All three entry points pre-guard:
  the web routes call `refuse_empty_filter` (same `filter_has_text` the engine
  uses) before queueing; `monitor.run_search` (monitor.py:114) skips empty filters;
  `gui.py` guards with `_filter_has_text` at lines 845/1865/1893/1931. The engine
  raise is a backstop, not the only guard, so no background thread or CLI run dies
  on an empty filter — the two empty "New Filter" entries in filters.json are
  skipped with a message under `--all`, not crashed on.
- **Source-label consolidation.** `_SOURCE_LABELS` is gone from the orchestrator;
  the one map is `config.SOURCE_LABELS` (now including `crossref`). The status line
  for `biorxiv_medrxiv` changes spelling ("bioRxiv/medRxiv" → "bioRxiv / medRxiv")
  but no consumer depends on that wording anymore — the structured
  `on_source_failure` carries the internal name, and the new test pins the new
  label. `FAILURE_STATUS_MARKER` is retained only as the human-facing suffix inside
  `_report_failure`; its remaining references are tests asserting the emission
  behaviour, not callers parsing it.
- **gui.py.** The single changed line removes the now-dead
  `from agents.search_agent import SearchAgent` import — a necessary consequence of
  S4 (without it gui.py fails at import). No other GUI behaviour changes: the
  SearchWorker path keeps its own empty-filter guard, and the discover dialog's
  empty-query case, which previously ran an unfiltered search, now surfaces a
  "Search failed: …" error instead — the M2 fix reaching the GUI, not a regression.

## Findings (cold pass, ranked) — all non-blocking, dispositioned

1. **P3 · tests/web/test_deploy_files.py:472 · S4's "legacy agent removed" scan
   covers code, not docs · confidence: high · severity: low.**
   The test asserts no `.py`/`run.sh` file references `search_agent`/`key_terms`,
   but the docs that still name the deleted entry point — `APP_SPEC.md:155`
   ("Entry point: python agents/search_agent.py"), plus `BUILD_SUMMARY.md`,
   `PROJECT_COMPLETE.md`, `GEMINI.md` — are never scanned. The plan's S4 scope
   named only "README, QUICKSTART, CLAUDE.md MVP list", all three of which ARE
   updated, so this is out-of-scope documentation drift, not a missed acceptance
   criterion. **Disposition:** record; fold into Batch 8's docs/cleanup pass
   (M34). No code or runtime impact.

2. **P2 · src/user_store.py:465 · the seed path still upserts (overwrites) ·
   confidence: med · severity: low.**
   `seed_filters_from_file` calls `upsert_filter` (ON CONFLICT(user_id, name) DO
   UPDATE), so the silent-overwrite the routes were fixed to prevent still exists
   on the new-account seed path. Concrete today: `filters.json` contains two
   identical "New Filter" entries, which collapse to one at sign-in. This is not
   user-data loss — a fresh account has nothing to lose — and the plan already
   schedules the fix: M32 (Batch 8) replaces the seed with a `filters.seed.json`
   that has no empty "New Filter" entries. **Disposition:** record; resolved by
   M32.

3. **P3 · src/user_store.py:411 · the "case-insensitive" guard folds ASCII only ·
   confidence: med · severity: low.**
   `filter_name_taken` compares `lower(name) = lower(?)` (SQLite `lower()` folds
   ASCII only) and the backing `UNIQUE(user_id, name)` (db.py:349) is case-sensitive,
   so a non-ASCII case pair (e.g. "CAFÉ"/"café") slips past the 409 and both
   create. The acceptance tests exercise only ASCII case. **Disposition:** record;
   an edge case for free-text filter names, not exercised by the acceptance
   criterion.

No medium-or-higher-severity defect was found in the code this batch changed. The
three findings are documentation drift, a pre-existing seed path already scheduled
for M32, and a Unicode edge case — none blocks the merge.

---

## Verdict: APPROVED

No medium-or-higher-severity findings against P1–P31 for the code in
`origin/main..HEAD`; P2, P3, P10, P19, P22, P31 were applicable and checked. Every
Batch-3 plan ID (A2, S2, B13, M2, S4) is implemented and pinned by a same-named
test asserting exact values, the two data-loss/overwrite hazards the batch existed
to close (route-level rename overwrite, per-front-end empty-filter guard) are
closed and tested, and the full suite is green (1473 passed, 1 skipped). The three
cold-pass findings are low severity and recorded with dispositions (out-of-scope
docs, M32-scheduled seed, Unicode edge); none reopens the gate.
