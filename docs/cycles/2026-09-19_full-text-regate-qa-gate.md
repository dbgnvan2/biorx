# Full-text summaries — Learning-QA re-gate (fix loop 1)

RANGE: 2c86177..HEAD (origin/main..HEAD, 10 commits)
COMMITS: 02411ab, 40c38fb, a0e5a00, 0122572, b73bcdf, 30bfb3b, 98c107c, 5597e5c, ba0993d, 111e35a
PLAN: docs/implementation_plan_2026-09-19_full_text.md (FT1, FT3, C1–C4)
PRIOR GATE: docs/cycles/2026-09-19_full-text-qa-gate.md — REJECTED on F1 (blocking) + F2–F5 (low).

APPLICABLE (checked): P1, P2, P5, P8 (LEARNINGS SSRF), P9, P10, P19, P21, P22, P25, P26,
P27, P31/P35, P36, LEARNINGS P9 (XSS sink).

---

## Fix verification (each finding → its fix → its test → mutation proof)

A cold reviewer (no knowledge of how the fix was written) traced every call site and
mutation-verified each new test — deleted/inverted the exact named line and confirmed the
test went red — before restoring the working tree. `git diff` against HEAD is clean for the
three touched source files after the sweep.

### F1 — operator's `find_by_title: false` is now honoured on the web (was blocking)
- Fix: `findByTitle()` (app.js:313-319) returns this browser's saved choice when it exists,
  else falls back to `state.findByTitleDefault`; `loadSources()` (app.js:511) reads
  `health.find_by_title_default` and re-syncs the checkbox (app.js:512). The POST body already
  used `findByTitle()` (app.js:1081), so the config default now propagates to the request.
- Test: `test_f1_find_by_title_falls_back_to_the_server_default` (test_frontend_wiring.py:1470,
  4 parametrized cases run the real function in node) + `test_f1_healthz_default_is_read_by_the_page`
  (test_frontend_wiring.py:1480, anchored source-line assertion). All 5 green.
- Mutation: `return state.findByTitleDefault !== false` → `return true` turned 3 of 4 cases red.

### F2 — 401/403 reported as "refused … check its settings", not as an outage
- Fix: `Refused` exception (fulltext.py:63) raised in `default_get_json` for 401/403
  (fulltext.py:171-172); caught in `find_full_text` (fulltext.py:330-333) and appended to
  `tried`, never to `unreachable`.
- Test: `test_f2_refusal_is_a_settings_problem_not_an_outage` (test_fulltext.py:201) — asserts
  the message lands in `tried` and Unpaywall stays out of `unreachable`. Green.
- Mutation: dropping the 401/403 mapping made the test see `SourceUnavailableError` (red).

### F3 — no DOI (or no email) is "skipped", not "no free copy"
- Fix: `unpaywall_urls` (fulltext.py:188-193) raises `Skip` for no email / no DOI instead of
  returning `[]`; the loop records `skipped (<reason>)` (fulltext.py:327-329). The removed
  `if urls is None` branch is dead — no finder returns `None` anymore (P22 clean).
- Test: `test_f3_no_doi_is_skipped_not_no_copy` (test_fulltext.py:216). Green.
- Mutation: reverting to `return []` for no-DOI made it red.

### F4 — no finder lookups fire after the download cap
- Fix: cap check moved to the top of the finder loop (fulltext.py:320-324); once
  `downloads >= max_downloads`, later finders are recorded "not asked (limit … reached)" and
  their `find()` is never called. Consistent `>=` with the inner loop guard (fulltext.py:346).
- Test: `test_f4_no_lookups_after_the_download_cap` (test_fulltext.py:223) — asserts zero
  OpenAlex/Semantic Scholar calls and the "not asked" note. Green.
- Mutation: removing the top cap check made `get.calls` non-empty (red).

### F5 — source_text/text_source migration is dirty-state tested
- Fix: `test_f5_summaries_source_columns_migrate_an_existing_db` (test_db_concurrency.py:295)
  builds an old-schema summaries table (drops the two columns), reopens, and asserts the columns
  return with `''` defaults and the pre-existing row is preserved.
- Mutation: removing the two `_add_column_if_missing` calls made it red ("no such column").

All 9 new tests pass individually; the full venv suite grew from 1081 to 1090 passed.

---

## NEW FINDINGS from the re-sweep (all LOW — backlog, not fixed in-loop)

### G1 — LOW · P1/P5 — 401/403 "settings problem" is applied source-agnostically
- Where: src/fulltext.py:171-172 (`default_get_json` raises `Refused` for any finder's 401/403).
- Scenario: the fix intended Unpaywall's 401/403 (invalid/unauthorized contact email) to read as
  a permanent settings error. But `default_get_json` is shared, so an OpenAlex or Semantic
  Scholar 403 — which can be a transient quota/rate signal, not a settings problem — is now
  reported as "refused … check its settings" instead of the retryable "could not be reached".
  Diagnostic precision only; the chain still proceeds correctly.
- Fix (backlog): special-case Unpaywall so only its 401/403 carries the "check its settings"
  hint, or keep the message neutral for the other finders.

### G2 — LOW · P21/P25 — config off-switch still overridden when /healthz is unreachable
- Where: web/static/app.js:71 (state.findByTitleDefault initialises `true`) + app.js:508-512
  (loadSources swallows errors).
- Scenario: if `/healthz` fails, `loadSources`'s catch leaves `state.findByTitleDefault` at
  `true`, so the client sends `find_by_title: true` and overrides the operator's
  `find_by_title: false` — the server-side config fallback (routes_summaries.py:132-133) never
  sees `None`. Narrow: the app is already degraded without sources, but the F1 fix is incomplete
  on this path.
- Fix (backlog): omit the `find_by_title` field when the server default is unknown, so the
  server's own config fallback applies.

### G3 — LOW · P10 — the checkbox re-sync itself is not test-anchored
- Where: tests/web/test_frontend_wiring.py:1480-1486 asserts only the
  `state.findByTitleDefault = health.find_by_title_default !== false` assignment, not the
  `$("find-by-title").checked = findByTitle()` re-sync at app.js:512.
- Scenario: removing app.js:512 would leave the Settings checkbox checked for operators who set
  `find_by_title: false` (the user-visible symptom F1 was about) while every test stays green.
  Coverage gap, not a defect.
- Fix (backlog): add an anchored assertion for the app.js:512 line.

---

## Test results

- venv/bin/python -m pytest tests/ -q  ->  1090 passed, 1 skipped, 4 warnings.
- /opt/homebrew/bin/pytest tests/ -q  ->  1062 passed, 19 skipped (18 PyQt6 GUI + 1), 4 warnings.

Both green. The single venv skip and the 19 homebrew skips are environment-dependent, not failures.

---

## VERDICT: APPROVED

The five fixes are correctly implemented, wired end-to-end, and each is backed by a
provable-failing regression test. The cold re-sweep found no defect of MEDIUM or above;
three LOW notes (G1–G3) are backlog items, not blockers.
