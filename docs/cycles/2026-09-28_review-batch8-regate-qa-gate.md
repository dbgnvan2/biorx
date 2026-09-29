# Review batch 8 — Learning-QA re-gate (2026-09-28)

Re-gate over `origin/main..HEAD` (now 7 commits), run to clear the batch-8
gate's two low findings. The batch-8 gate (`2026-09-28_review-batch8-qa-gate.md`,
APPROVED) reviewed the first 6 commits and found two LOW findings; the 7th
commit (`0bf9ddb`) answers them, and per the sweep's own rule fix commits are
unreviewed code, so that commit is re-swept here.

RANGE:     origin/main..HEAD (three-dot vs merge base 20fa182)
COMMITS:   7 — 0bf9ddb (batch-8 gate findings), a9f2252 (batch-1 live
           after-run + coverage report), ddf3972 (monitor source message),
           4f1c4db (delete upsert_filter, M34), 1516b4b (batch-6 gate notes
           2/3), 4567c19 (M19 test), 42ca4ca (batch 8 fixes). The last one is
           new since the batch-8 gate; the other six are unchanged.
APPLICABLE: P19 (producer/consumer folding drift), P26 (fix-commit pattern),
           plus the batch-8 set P2, P5, P6, P13, P14, P22, P27, P29, P32 for
           the six commits already approved.
CHECKED:    P1–P14 (repo playbook) + generic P5, P6, P19, P22, P26, P27, P29,
           P32, and a differential probe of the new fold helper against
           SQLite's lower().
NOT COVERED: logic correctness, auth, performance, concurrency beyond the
           M30/M35 paths; the retiring gui.py (plan section 10); live-API
           human checks (retrieval_after.md).

## Test suite

`venv/bin/python -m pytest tests/ -q` → **1567 passed, 2 skipped** (70 s).
Skips: `test_fulltext.py:294` (RLIMIT_AS Linux-only) and
`test_h_environment.py:588` (CI Python-version check) — the plan's CI-only
checks. The new `tests/test_db_migrations.py::test_t2_non_ascii_names_fold_as_each_comparison_does`
runs and passes; `tests/test_db_migrations.py` runs green on its own (4 passed).

## Finding 1 — resolved

`docs/spec_coverage_review_fixes.md` now reads "Suite after the batch-8 gate
fixes, on the owner's Mac: 1567 passed, 2 skipped ... A third test skips only
on a machine with no local filters.json, such as CI." The measured run is
exactly 1567 passed / 2 skipped, so the claim matches the machine. The
"filters.json where absent" third skip is now described as the CI case, not
folded into the owner's count. No discrepancy remains.

## Finding 2 — judged, and the suggested fix was correctly not taken

The batch-8 gate suggested folding login names with `.casefold()` in
`_rename_case_duplicate_logins`, arguing it was the same class as the
filter-name casefold fix. That direction is wrong; the user's reasoning is
right. The two folds are not one "case-insensitive" concept — each comparison
must match the consumer that enforces it:

- **Login names** are compared by SQLite's `lower()` in every enforcement
  point: the unique index `ON users(lower(login_name))` (src/db.py:516),
  sign-in `WHERE lower(login_name) = lower(?)` (src/accounts.py:132), and
  access-code lookups (src/access_codes.py:425, 722). SQLite's `lower()` folds
  ASCII only, so `ÉRIN` and `érin` are two distinct accounts to every one of
  those comparisons. A casefold-based rename would rename a legitimate,
  distinct account (`érin` → `érin (2)`) for no index or sign-in benefit — an
  over-rename that mutates user data.
- **Filter names** are compared by `filter_name_taken` with Python `casefold`
  (src/user_store.py:409, 414), so `CAFÉ`/`café` are the same name there and
  must be reported as a case-duplicate.

The commit does exactly this: login rename folds through `_sqlite_lower`
(ASCII-only translate), the filter report folds through `casefold`. The pre-fix
code folded login names with Python `str.lower()`, which — being Unicode-aware —
folded *more* than SQLite's `lower()` and therefore shared the very over-rename
bug the gate's casefold suggestion would have amplified (casefold folds more
aggressively still: ß→ss, İ→i̇). The fix is the correct, narrower direction.

Differential check: `_sqlite_lower` was probed against `SELECT lower(?)` in a
live SQLite connection over 26 edge-case strings (É/é, ß, İ/ı, fullwidth
ＡＢＣ, ǅ/ǆ, mixed alnum). **0 mismatches** — the Python mirror is byte-equivalent
to the SQLite builtin it stands in for, and by construction (an A–Z→a–z
translate table) it cannot fold anything SQLite's `lower()` leaves alone.

The new regression test asserts the exact distinction: `ÉRIN`/`érin` are NOT
renamed and produce no ERROR, while `CAFÉ`/`café` IS reported as a filter
case-duplicate — asserting `==` on the unchanged rows, not a floor.

## FINDINGS (ranked)

None.

The fix commit is confined to the three files it names
(`docs/spec_coverage_review_fixes.md`, `src/db.py`, `tests/test_db_migrations.py`,
+53/−13) and introduces no new defect: the rename keeps its `ORDER BY
created_at, rowid` oldest-keeps-name ordering, the suffix-probe loop stays
consistent with the `taken` set (folded keys on both sides), `Dict` is already
imported for the annotation, and the `_ASCII_LOWER` table is referenced at call
time so its position below the function is harmless.

Minor observation (not a finding): `_sqlite_lower` is a hand-written mirror of
a SQLite builtin rather than SQLite itself computing the key (e.g. selecting
`lower(login_name)`). It cannot drift — SQLite's `lower()` is a stable,
documented ASCII-only fold, the helper is differential-verified above, and a
test now pins the exact non-ASCII behaviour.

VERDICT: clean against P1–P14 (and the generic patterns examined), of which
P19 and P26 were applicable to the fix commit — with 0 findings.

**APPROVED**
