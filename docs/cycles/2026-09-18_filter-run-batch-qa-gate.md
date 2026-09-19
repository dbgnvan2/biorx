# QA gate — filter-run batch (learning-qa sweep)

**Date:** 2026-09-18
**Repo:** biorx
**Range:** `179f83f..HEAD` (26 commits, `origin/main..HEAD`)
**Reviewer:** learning-qa failure-pattern sweep (P1–P36 generic + repo LEARNINGS P1–P12)

## RANGE

```
git diff 179f83f..HEAD   (materialized at /tmp/sweep.diff, 4,200 lines)
BASE   179f83f36e379efd7b32baf76ceafb7fe47ecb6b  (origin/main)
HEAD   41a0df48ab067a9f50e2c5e05aff6654b6a06d97
```

Working tree is dirty but out of range: `filters.json` modified, `Maternal
nueroticism.pdf` deleted, untracked `.claude/`, `.test-qa-report.md`. None are
part of the reviewed range; the 56-file diff is clean.

## COMMITS

26 commits, summarized by theme:
- `17feae0`→`b26e9c2` docs: plan + LEARNINGS.md for the filter-run defects
- `6dc5120`→`babb276` filter-run features (FR1 empty-refusal, FR2 enrichment
  scope, FR3 live counts, FR4 Run-button states)
- `dae973f`→`3262bbf` review findings 1–7 (enrichment outages surface, no
  silent downgrade, .env loading, blank-summary guard, unreachable-source
  reporting, dead worker removal, paid-model naming)
- `f778746`→`20c47e3` issues 1–5 (saved-filter sources, enrichment fields reach
  results, Crossref/Unpaywall outages reported, GUI skip announcement,
  Filters-tab test survives a failed check)
- `1e098ab`→`c86a879` provider-default rework (DeepSeek default, DEFAULT_LLM_PROVIDER)
- `215f739`→`41a0df4` saved-reference-lists (RL1 details/summaries, RL2
  summarize, RL3 ticked export, list-summaries route inventory)

## TEST RESULTS

Both environments green.

- `venv/bin/python -m pytest tests/ -q` → **1047 passed, 1 skipped** (44.7s)
- `/opt/homebrew/bin/pytest tests/ -q` → **1019 passed, 19 skipped** (41.0s);
  18 GUI files/tests skipped for want of PyQt6, announced by the new
  `pytest_terminal_summary` (issue 2 fix). Non-GUI count 1019 == 1047 − 28
  (PyQt6-dependent cases), consistent.

No `PytestReturnNotNoneWarning`, no `return`-in-test, no floor-only assertions
introduced (spot-checked new tests; all assert exact values).

## APPLICABLE

Generic P1, P2, P5, P6, P8, P10, P13, P15, P19, P21, P22, P25, P27, P28, P29,
P31, P34, P36. Repo-local: P8 (SSRF), P9 (XSS), P10 (guard in one front end),
P11 (stage no reader), P12 (one channel, two quantities).

## CHECKED (per pattern)

- P1/P2 — enrich adapters now return `bool`; `_enrich` counts and reports
  outages via WARNING + `on_status` + `on_enrich_problem`; monitor exits 2 on
  enrichment problems. `shouldStopPolling` (JS) retries transient blips, gives
  up only on 401/404/410 or 8 consecutive failures. Verified: no silent drop
  path remains; empty result vs failed is distinguishable.
- P5 — Crossref AND Unpaywall both hardened identically (return-contract +
  counting); `paper_meta._crossref_abstract` raises on the new `False`.
- P6 — `OllamaClient.is_available()` now checks the model is installed, not
  just that the server answers (the qwen:7b false-positive).
- P8 — no new persisted-state reader; the Run-button state survives re-render
  (FR4.3 test) and the second-run/cached path is exercised in the node harness.
- P10 — empty-filter refusal lives in shared `filter_has_text` and is wired in
  web routes, monitor, and GUI; tested per entry point (FR1.1–1.6).
- P13/P15 — `SearchWorker.run` keeps its top-level try/except + DB release;
  the removed `SummarizationWorker` was dead code (verified no callers).
- P19 — `filter_has_text` derives from `normalise_filter`/`filter_papers`
  semantics, not a parallel hand-copy; adversarial case
  `{"text_groups":[{"both":""}],"keywords":["stress"]}` → False is pinned.
- P21/P25 — enrichment results reach the web results AND the GUI table (real
  orchestrator, network faked); Run buttons pass `filter_id`, not the panel's
  source boxes.
- P22 — `enrich()` return-type change (None→bool) grepped; both non-test
  callers (`orchestrator._enrich`, `paper_meta._crossref_abstract`) updated to
  `is False`; no stale `if not enrich()` branch found.
- P27/P29 — new tests assert exact values; `PROTECTED_ROUTE_COUNT` exact 31→32.
- P28/P34 — autouse conftest fixture redirects `env_file.PROJECT_ENV` to a
  missing file and deletes `DEFAULT_LLM_PROVIDER`; entry-point env-load tests
  monkeypatch `load_project_env`.
- P31/P36 — enrichment output now asserted in what the user receives
  (`test_fr2_3`, `test_fr3_1`, `test_i4_gui_rows_get_enriched_fields`).

## NOT COVERED

- P9 (XSS) — the new `app.js` DOM insertions (`detailBtn`, `✓ Summary` badge,
  `enrich-problems`) all use `textContent`/`createElement`, not `innerHTML`.
  Verified by reading the diff; no new sink. (Repo LEARNINGS P9 is a standing
  open risk, unchanged by this diff.)
- P8/P34 SSRF — no new server-side URL proxy in this diff.
- P20/P23 — no LLM-judge or single-draw stochastic regression introduced.

## FINDINGS

Ranked. None medium or higher; two low, two informational. No regressions
introduced — both low findings are residuals/coverage gaps, not defects.

**1. LOW — `normalise_filter` is not applied in shared code; the keywords-string
letter-split survives in the GUI/monitor query-builder path.**
- Where: `gui.py:144-178` (`SearchWorker.run` passes raw `f`), `agents/monitor.py:114-146`
  (`run_search` passes raw `filter_dict`); `src/filtering.py:51-53` converts a
  top-level `keywords` string to a list, but only `web/routes_searches.py:125`
  and `user_store.py` call `normalise_filter` before the query is built.
- Failure scenario: a filter whose only criterion is a top-level
  `"keywords": "stress, cortisol"` string (legacy/hand-written filters.json) is
  allowed by `filter_has_text` (it normalises internally → "has text") but the
  query builders (`build_europepmc_query` et al., `query_builder.py:118-119`)
  do `", ".join(filter_dict["keywords"])` on the *unnormalised* string, yielding
  `"s, t, r, e, s, s"` — a garbage query that fetches nothing, so a genuinely
  matching paper is never retrieved. The docstring on `normalise_filter` claims
  it is applied "wherever a stored or submitted filter is read or run", which
  the GUI and monitor paths falsify.
- Severity: LOW (edge — the GUI editor writes `text_groups`, not top-level
  `keywords`; web-saved filters are normalised at save; only hand-edited
  filters.json hits it).
- Fix: normalise once inside `SourceOrchestrator.search()` (the shared entry
  every front end goes through — repo P10), or at minimum in
  `monitor.run_search` and `SearchWorker.run` before `orchestrator.search`.

**2. LOW — identity-based `enrich_only`/`matched` verified on the single-source
path only; the cross-source dedup-merge path is untested (pre-existing).**
- Where: `web/routes_searches.py:126-141` and `gui.py:154-163` link on_batch
  records to enrichment via `id()`; correctness rests on `Deduplicator.add`
  returning/mutating the same object (confirmed in `src/sources/dedup.py:171,179`).
- Failure scenario: a paper first seen from a source whose record *fails* the
  filter, then re-seen from a richer source whose record *passes*. The duplicate
  is merged into the first-seen object and is **not** re-streamed (`_search_source`
  `continue`s on `len(dedup) == before`), so it never enters `matched`/`matched_ids`
  and is silently dropped from results — even though the merged record would now
  match. This is **not** introduced by this diff (the old on_batch snapshot had
  the same ordering), but the diff rewrites exactly this code and the fixtures
  (`_real_orchestrator`, `_two_record_orch`, `_make_record`) use one source with
  non-colliding DOIs, so the merge path has zero coverage.
- Severity: LOW (pre-existing behaviour, unchanged; flagged because the diff
  made `matched` record objects the single source of truth for results).
- Fix: add a two-source test — same DOI, first source filtered out, second
  matches — asserting the merged paper reaches results and is enriched.

**3. INFO — conftest deletes `DEFAULT_LLM_PROVIDER` but not the sibling
`LLM_PROVIDER`.**
- Where: `tests/conftest.py:1794-1807` (autouse `_never_load_the_real_env_file`).
- The new `_provider_setting` reads `DEFAULT_LLM_PROVIDER` before `LLM_PROVIDER`,
  and the fixture deletes the former to stop a developer's shell value
  outranking the tests. `LLM_PROVIDER` is left to per-test `delenv`, which every
  affected test currently does (`test_llm_config`, `test_m1_repo_default_is_deepseek`).
  A future test that forgets to clear `LLM_PROVIDER` would silently inherit the
  developer's setting (P5 sibling-asymmetry). No current test breaks.
- Severity: INFO.
- Fix (optional): `monkeypatch.delenv("LLM_PROVIDER", raising=False)` alongside.

**4. INFO — `_enrich` "failed for N of M" uses records-attempted as the
denominator for two independent services.**
- Where: `src/sources/orchestrator.py:444-473`.
- `attempted` counts records, not per-service lookups; a record with a DOI
  attempts both Crossref and Unpaywall, so "Crossref failed for 12 of 124
  papers" is correct per record but "124" is the record count, not the Crossref
  lookup count. Harmless and arguably clearer to users; noted only so the
  quantity is not mistaken for a per-service total later (repo P12: one
  quantity per field — here two services share one denominator).
- Severity: INFO. No change required.

## VERDICT

**APPROVED.**

Both test environments are green (1047 and 1019 passed), no medium-or-higher
finding, no regression introduced. Findings 1 and 2 are residuals (documented
edge paths and coverage gaps), not defects in the shipped behaviour; both carry
concrete, low-risk follow-ups. The diff implements its plan faithfully with
exact-value tests for every acceptance criterion and the repo's P10/P11/P12
patterns visibly fixed.
