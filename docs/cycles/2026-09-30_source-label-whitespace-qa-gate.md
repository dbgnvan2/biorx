# QA Gate — Whitespace-only source label fallback (commit 021e10e)

- **Date:** 2026-09-30
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — adversarial review by the gate
  author, reading the materialized diff hunk-by-hunk, the changed files in full
  context, and the production data the change is meant to protect.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1606 passed, 2 skipped**
  (68.21 s; 4 warnings, all Starlette/AnyIO deprecation notices).

RANGE:       origin/main..HEAD. origin/main is 0 behind, so the two-dot diff equals
             the merge-base diff. Materialized with `git diff origin/main..HEAD`
             and read in full; changed files then read in full context.
COMMITS:     1 commit:
             021e10e fix: a whitespace-only source name falls back, on the page and in exports
APPLICABLE:  P5 (harden all sibling calls — every renderer of the source label must
             share the fix), P19 (producer/consumer drift — a Python port of the
             page's `paperSourceText` that must not diverge from it), P26 (a fix
             commit that could itself regress something subtler).
CHECKED:     P5, P19, P26. Read the full diff (4 files) and the changed source
             files (`src/reference_export.py`, `web/static/app.js`) plus their
             tests. Enumerated every source-label renderer on both sides: all four
             export paths (reference-list CSV/RTF/PDF via `render`→`_row`; both
             summaries-PDF routes via `build_summaries_pdf`→`meta_of`) and every
             JS table cell. Queried the production DB (`~/preprints/biorxiv.db`,
             2,624 papers) for the real field values and the `server == source`
             invariant. Traced both write paths (`CanonicalRecord.to_dict` and the
             legacy `biorxiv_api.parse_papers`) to prove the invariant is
             structural, not coincidental. Ran the full suite green; reverted the
             two source files to `origin/main` and confirmed the new tests fail,
             then restored (tree clean).
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness beyond label selection, concurrency, auth/authz,
             injection & security, performance, dependency risk, API-contract
             compatibility, architecture. Live provider behaviour is exercised by
             fixtures, not live APIs. Cross-language equivalence is checked by
             reasoning plus the overlapping test batteries, not by executing app.js
             against every conceivable JSON shape.

---

## The change, and what it is for

The prior gate (commit 5ef39e6) made `source_label` =
`(journal_or_server or source or server or "").strip()` and noted two low-severity
drifts from the page's `paperSourceText` = `journal_or_server || source || ""`.
This commit closes both:

- **Whitespace-only values no longer count as a label.** Truthiness was tested
  before stripping, so a `journal_or_server` of `"   "` stripped to `""` instead
  of falling through to `source`. Both functions now strip/trim each candidate
  and test the result.
- **The dead `server` fallback is dropped.** `source_label` no longer reads
  `server`; the docstring records that old rows default it to `"biorxiv"`.

`source_label` (Python) and `paperSourceText` (JS) are now word-for-word the same
rule: try `journal_or_server`, then `source`; stringify, strip, skip if blank;
return `""` if neither survives.

## Verification by check

**The two functions agree on every input shape.** Both iterate the same two keys
in the same order, stringify with `str()`/`String()`, strip/trim, and skip falsy
results. The falsy-collapse is equivalent across the language boundary:
`paper.get(k) or ""` and `value || ""` both map `None`/`undefined`, `""`, `0`,
`false` and whitespace to `""`, and pass numbers through to `str()`/`String()`.
Missing keys behave identically (`None`→`""`, `undefined`→`""`). The new test
cases overlap across both batteries — `test_sl1_*` (Python) and `test_br6_*`
(node) both assert the whitespace-fallthrough, strip, and server-only-→-`""`
shapes and pass.

**Dropping the `server` fallback cannot blank a label for any real stored row.**
The production DB holds 2,624 papers; for every one `server == source` (0
exceptions), `source` is never blank or whitespace-only, and `journal_or_server`
is never whitespace-only. The dangerous shape — blank `journal_or_server` AND
blank `source` with a non-blank `server` — has 0 rows. The invariant is also
structural, not a coincidence of today's data: `CanonicalRecord.to_dict()` emits
only `source` (never `server`), the legacy `biorxiv_api.parse_papers()` emits only
`server` (never `source`), and `db.insert_paper` cross-defaults the two columns to
each other and to `"biorxiv"` (schema default). Either way the two columns are
written equal, so the removed `server` level could only ever have selected a value
already returned by the `source` level. The `server` fallback was dead code.

**Both tables and every export use them.** All four export paths route through
`source_label`: `reference_export.render` (CSV/RTF/PDF, both `/save` and the
legacy `/export.csv`), and `summary_pdf.build_summaries_pdf` (both the
reference-list and search-results summaries PDFs). The two main page tables — the
search results table (app.js:1071) and the reference-list table (app.js:2281) —
route through `paperSourceText`. One renderer does not; see Finding 1.

**Tests fail on the pre-fix code.** Reverting `src/reference_export.py` and
`web/static/app.js` to `origin/main` and running the changed tests yields **3
failed**: `test_sl1_source_label_matches_the_page[paper3-]` (server-only→`""`),
`[paper4-europepmc]` (whitespace fallthrough), and
`test_br6_both_tables_label_the_source_the_same_way`. All three are the new
assertions this commit adds; the pre-existing cases still pass. Restored to HEAD;
the tree is clean and the full suite is green.

**Nothing else changed.** `git diff origin/main..HEAD --stat` is exactly 4 files
(2 source, 2 test), +27 / −10. No config, schema, or documentation files were
touched.

## Findings (ranked) — non-blocking

1. **P5 · web/static/app.js:1927 · a third source-label renderer does not share
   the fix · confidence: high · severity: low.**
   The "Filter test" results table (`renderFilterTestResults`) renders its Source
   column inline — `tag.textContent = p.journal_or_server || p.source || ""` —
   rather than through `paperSourceText`. It therefore does not get the
   whitespace fallthrough or the strip this commit adds, and it still shows a
   whitespace-only `journal_or_server` as a blank tag where the search results
   and reference-list tables would now fall through to `source`. This is a
   live-results view of the same papers, so the inconsistency is real, not
   hypothetical. **Impact is low:** the whitespace-only case has 0 rows in
   production and no adapter today emits a whitespace-only `journal_or_server`
   (europepmc passes `journalTitle` through untrimmed, which is the one plausible
   future source); the row is pre-existing and untouched by this commit, which
   introduced no regression here. The `test_br6` assertion
   `code.count("tag.textContent = paperSourceText(") == 2` encodes the "two
   tables" assumption and so does not notice this third renderer.
   **Disposition:** record; route line 1927 through `paperSourceText` in a
   follow-up so all three page renderers share one rule (P5), and widen the count
   assertion to 3 when that lands. Out of this commit's stated scope.

No medium-or-higher-severity defect was found in the code this commit changed.
The commit is exactly what it claims: two functions brought to one identical rule,
a provably-dead fallback level removed, tests added and green, nothing else
touched.

---

## Verdict: APPROVED
