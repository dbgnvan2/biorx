# Review batch 8 — Learning-QA gate (2026-09-28)

Range reviewed: `origin/main..HEAD` (6 commits), per the caller-supplied range.
Diff materialised: `git diff origin/main...HEAD` (43 files, +1192/−810).

RANGE:     origin/main..HEAD (three-dot vs merge base 20fa182)
COMMITS:   6 — 42ca4ca (batch 8 fixes), 4567c19 (M19 test), 1516b4b (batch-6 gate
           notes 2/3), 4f1c4db (delete upsert_filter, M34), ddf3972 (monitor
           source message), a9f2252 (coverage report + retrieval-after docs)
APPLICABLE: P2, P5, P6, P13, P14, P19, P22, P27, P29, P32
CHECKED:    P1–P14 (repo playbook) + generic P5, P6, P19, P22, P27, P29, P32, P26
NOT COVERED: logic correctness, auth, performance, concurrency beyond the M30/
           M35 paths; the retiring gui.py (plan section 10 records known
           regressions); live-API human checks (recorded in retrieval_after.md).

## Test suite

`venv/bin/python -m pytest tests/ -q` → **1566 passed, 2 skipped** (70 s).
Skips: `test_fulltext.py:294` (RLIMIT_AS Linux-only) and
`test_h_environment.py:588` (CI Python-version check). Both are the plan's
"CI-only" checks, as intended.

## Coverage-report verification (asked for explicitly)

`docs/spec_coverage_review_fixes.md` claims "1565 passed, 3 skipped (Linux-only
memory limit, CI-only Python version check, and the owner's local filters.json
where absent)". The actual run on this machine is **1566 passed, 2 skipped**:
the third skip does not fire because the owner's `filters.json` is present
(7495 bytes, gitignored). The report's count is the CI / no-filters.json
figure, not the owner's-machine run. See finding 1.

Every batch-8 evidence test named in the report exists and passes; the batch-1
through batch-7 rows were spot-checked (M38 import_module confirmed at
`tests/web/test_requirements.py:75`; M34 `src/selection.py` still imported by
`gui.py:33`; no remaining caller of `upsert_filter` or the deleted bookmark
methods outside docs/tests). No fabricated test name found.

## FINDINGS (ranked)

1. LOW · docs/spec_coverage_review_fixes.md:11 · the suite count does not match
   the repo · "1565 passed, 3 skipped" vs the actual 1566/2; the "filters.json
   where absent" skip cannot fire on the machine where filters.json exists, and
   the parenthetical contradicts "the owner's copy was preserved" · fix: state
   "1566 passed, 2 skipped (CI-only memory limit + Python-version checks); a
   third test skips only where the local filters.json is absent" · confidence:
   high (measured, not inferred).

2. LOW · src/db.py:585-598, 618-631 · login-name case folding is ASCII-only
   (`lower()` + the `lower(login_name)` index) while filter-name folding was
   just fixed to `casefold` in this same batch (batch-3 gate note 3) · the same
   class — "SQLite's lower() folds ASCII only" — was fixed in user_store
   (`filter_name_taken` → casefold) but not propagated to the new migration's
   sibling comparison, so non-ASCII case-duplicate login names (and pre-existing
   non-ASCII case-duplicate filters) are neither renamed nor reported · fix:
   compare login names with `.casefold()` in `_rename_case_duplicate_logins`
   (the index can stay `lower(login_name)`, which is what it enforces) · no
   crash and no data loss today — the rename and the index agree on ASCII ·
   confidence: high that it is inconsistent, low that it matters.

No medium-or-higher findings. The two substantive fixes in this batch both
check out on inspection and by their tests:

- **M35 double-billing**: `setBatchRunning(true)` is now claimed synchronously
  before the first `await` in both `summarizeChecked` and `reviewChecked`, with
  release in `finally` on every path. `test_m35_double_click_single_run` drives
  the real functions in node and proves one run, not two.
- **M30 foreign keys ON**: the `conn` property turns `PRAGMA foreign_keys = ON`
  per connection; `_relax_doi_not_null` correctly toggles FK OFF *before*
  `BEGIN IMMEDIATE` and back ON in `finally` after COMMIT/ROLLBACK (so the
  pragma is not a silent no-op inside a transaction). No production
  parent-delete lacks a cascade or guard: no `DELETE FROM users`/`papers` in
  src/ or web/, `delete_reference_list` relies on the schema's ON DELETE CASCADE
  (both child tables), and orphan cleanup is logged, never silent.

VERDICT: clean against P1–P14 (and the generic patterns examined), of which
P2, P5, P6, P13, P14, P19, P22, P27, P29, P32 were applicable — with 2
low-severity findings (both docs/consistency, neither a code defect).

**APPROVED**

(Recommendation carried into the verdict: correct the suite count in
docs/spec_coverage_review_fixes.md to match this machine's 1566/2 before the
report is treated as final; finding 2 is optional and low-impact.)
