# QA Gate — Source label in exports (commit 5ef39e6)

- **Date:** 2026-09-30
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — adversarial review by the gate
  author, reading the materialized diff hunk-by-hunk, the changed files in full
  context, and the production data the change is meant to correct.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1603 passed, 2 skipped**
  (70.67 s; 4 warnings, all Starlette/AnyIO deprecation notices).

RANGE:       origin/main..HEAD. origin/main is 0 behind, so the two-dot diff equals
             the merge-base diff. Materialized with `git diff origin/main..HEAD`
             and read in full; changed files then read in full context.
COMMITS:     1 commit:
             5ef39e6 fix: exports name the journal or server, not the internal source id
APPLICABLE:  P5 (harden all sibling calls — both export paths must share the fix),
             P19 (producer/consumer drift — a Python port of the page's
             `paperSourceText` that must not diverge from it).
CHECKED:     P5, P19. Read the full diff (4 files) and the changed source files
             (`src/reference_export.py`, `src/summary_pdf.py`) plus their tests.
             Traced both export paths (reference-list CSV/RTF/PDF via `_row`,
             summaries PDF via `meta_of`) to confirm a single shared
             `source_label()` drives both. Read `web/static/app.js`
             `paperSourceText` (the producer being mirrored) and every source
             adapter's `journal_or_server`/`source`/`server` population. Queried
             the production DB (`~/preprints/biorxiv.db`) for the real field
             values. Imported both modules in both orders to prove no cycle. Ran
             the full suite green; reverted the two source files to `origin/main`
             and confirmed every `test_sl1_*` fails, then restored.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness beyond the label selection, concurrency, auth/authz,
             injection & security, performance, dependency risk, API-contract
             compatibility, architecture. Live provider behaviour is exercised by
             fixtures, not live APIs. Cross-language equivalence is checked by
             reasoning + the production data, not by executing app.js.

---

## The change, and what it is for

Before this commit, both export paths rendered a paper's provenance with
`source or server` — the stored internal id (`europepmc`, `biorxiv_medrxiv`,
…), which is not what the page shows. The page (`app.js paperSourceText`)
renders `paper.journal_or_server || paper.source || ""`, i.e. the journal or
server display name first. The commit adds one shared `source_label()` in
`src/reference_export.py` and routes both renderers through it:

- `reference_export._row` (CSV, RTF and PDF all build their rows here) → `source_label`.
- `summary_pdf.meta_of` → `source_label` (imported lazily inside
  `build_summaries_pdf`, mirroring the existing lazy import in `to_pdf`).

`source_label` = `(journal_or_server or source or server or "").strip()`.

## Verification by check

**Label matches the page for every source.** The six search sources populate
`journal_or_server` as: europepmc (journal title, else ""), pubmed ("<journal>
(PubMed)" else "PubMed"), psyarxiv ("PsyArXiv"), socarxiv ("SocArXiv"),
biorxiv_medrxiv ("bioRxiv"/"medRxiv"), arxiv ("arXiv"). `source` is always the
internal id. The production DB (2,624 papers) confirms the pattern: for every
row, `server` equals `source`, and `journal_or_server` is either a display name
or empty. Both the page and the export therefore select the same string for
every source present in the data: display name when set, else the internal id
as the shared fallback. The bug the commit targets is visible in the data — 9
`biorxiv_medrxiv` rows whose `journal_or_server` is `bioRxiv`, where the old
code printed `biorxiv_medrxiv` while the page showed `bioRxiv`.

**No import cycle.** `reference_export` imports `summary_pdf` at module top (for
`FONT_ENV`); `summary_pdf` imports `reference_export` only inside
`build_summaries_pdf`. Importing each module first works; neither triggers a
partial-module read of the other. The lazy import follows the file's own
established convention (`to_pdf` already lazily imports `summary_pdf`).

**Blank/whitespace fallback.** Blank (`None`/`""`) `journal_or_server` falls
through to `source` correctly — verified for the 2,373 europepmc rows whose
`journal_or_server` is empty (both page and export show `europepmc`).
Whitespace-only does not: see Finding 1.

**Tests fail on the pre-fix code.** Reverting `src/reference_export.py` and
`src/summary_pdf.py` to `origin/main` and running the three `test_sl1_*` tests
yields **7 failed** (the parametrized label test ×5, the every-format test, and
the summaries-PDF test), with the summaries test reproducing the exact
production symptom (`· biorxiv_medrxiv` present in the PDF text). Restored to
HEAD; the tree is clean and the full suite is green.

**Nothing else changed.** `git diff origin/main..HEAD --stat` is exactly 4
files (2 source, 2 test), +57 / −2. No other source, config, or documentation
files were touched.

## Findings (ranked) — all non-blocking

1. **P19 · src/reference_export.py:57 · a whitespace-only `journal_or_server`
   does not fall back · confidence: high · severity: low.**
   Truthiness is tested before `.strip()`, so a value of `"   "` is treated as
   a real label and strips to `""` instead of falling through to `source`
   (`source_label({"journal_or_server": "   ", "source": "europepmc"})` returns
   `""`, not `"europepmc"`). The blank case is covered by a test; the whitespace
   case is not. **No production impact:** the DB has 0 whitespace-only rows, and
   `db.py`'s startup migration `TRIM(journal_or_server)` collapses any that
   appear to `""` before the export reads them. Note also the page itself does
   not fall back on whitespace (JS `"   " || source` yields `"   "`), so
   "fall back on whitespace" and "match the page" are mutually exclusive here.
   **Disposition:** record; a one-line hardening (strip each candidate before
   testing truthiness) if a future adapter ever emits whitespace-only names.

2. **P19 · src/reference_export.py:53-58 · the port is not byte-identical to
   `paperSourceText` · confidence: high · severity: low.**
   The Python adds a `server` fallback and a `.strip()` that the JS omits, while
   the docstring claims "as the page shows it". Both additions are inert or
   beneficial: `server` provably equals `source` in every production row, so the
   extra level can never select a different value; and `.strip()` only improves
   the 212 stale `" (PubMed)"` rows (page shows the stray leading space, the
   export does not — and a separate `db.py` migration already targets those
   rows). This is a hand-port of browser code that cannot be imported into
   Python, so a faithful copy is the best available single-source; the drift is
   cosmetic and one-directional (export ≤ page noise). **Disposition:** record;
   acceptable given the `server == source` invariant, but the docstring could
   say "the journal/server name first, else the stored source" without claiming
   byte-parity with app.js.

No medium-or-higher-severity defect was found in the code this commit changed.
Both findings are low severity: one is unreachable in production data, the other
is a cosmetic improvement over the page with a known-to-be-dead fallback level.

---

## Verdict: APPROVED
