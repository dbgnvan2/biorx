# QA gate — gate-notes follow-ups (2026-10-08, GN4–GN6)

RANGE:   origin/main...HEAD (2 commits)
COMMITS: 1b46008 docs: plan GN4-GN5 - gate-notes gate TODO items
         95da0f2 fix: gate-notes follow-ups (GN4-GN6)

Plan: docs/implementation_plan_2026-10-08_gate_notes_2.md
Protected path touched: none. `src/filtering.py` and `src/sources/config.py`
are not on the protected list; `test_no_retrieval_drift.py` is green in the full
suite (empty protected diff at HEAD), and the orchestrator is unchanged.

Reviewer: learning-qa — one verification pass by the gate runner over the
materialized diff (origin/main...HEAD), tracing the three named gate checks
against the real producers (schema.keyword_list, the orchestrator's registration
path, the filter's matcher, and db.Database.conn).

## Verdict: APPROVED

All three named gate checks pass, the full suite is green, and no
MEDIUM-or-higher finding remains. The change closes the three backlog items
(GN4–GN6) exactly as the plan describes: one shared keyword reader, one exact
three-way source-list agreement, and one flake-free connection test. No fix
commit beyond the gate itself is needed, so the loop's stopping condition (no
finding of MEDIUM-or-higher requiring a code change) is met. The four remaining
notes are below-medium and go to TODO.

---

## APPLICABLE / CHECKED

APPLICABLE:  P26 (fix commits are the least-reviewed code), P27 (a test that
cannot fail), P29 (a floor assertion that hides narrowing), P19 corollary
(asserting on source text vs behaviour).
CHECKED:     P1–P37 as relevant to a pure refactor + test-only change; no
external calls, no I/O shape changes, no scoring, no background workers.
NOT COVERED: caller did not restrict paths. Scope-limit families not assessed:
logic/algorithmic correctness beyond the three named checks, concurrency races
beyond the db.conn identity semantics, security, dependency risk.

## The three named gate checks

1. GN4 — `keyword_fields` delegates to `schema.keyword_list`, no matching
   behaviour change. PASS.
   `src/filtering.py:193` now returns `keyword_list(paper.get("keywords"))`.
   The old body differed from `keyword_list` only by (a) `paper.get(...) or []`
   and (b) returning `k` instead of `k.strip()`. (a) is provably identical for
   every input shape (None/""/[]/0/{}/False all reach the same `[]`), and (b)
   is matching-neutral: `term_matches` normalises every field through
   `normalise_text` (src/search_terms.py:44), which lowercases, collapses
   whitespace runs, and `.strip()`s — so `"  Parts  "` and `"Parts"` both
   become `"parts"` before `match_term` runs. Both callers (text_group_matches
   at filtering.py:232 and within_matches at :303) pass keywords into
   term_matches; no caller consumes the raw strings for display or query
   building. The GN4 test pins the one real past divergence (strip) and the
   single-string-is-one-keyword path.

2. GN5 — the GN1 test ties the three source lists exactly. PASS.
   tests/test_limit_summary.py:129 now asserts
   `registered == set(_SEARCH_SOURCES) - set(SOURCES_WITHOUT_ADAPTER)` and
   `set().union(*lists) == registered`, with the per-name `sum(...) == 1`
   classification check kept. The `len(registered) >= 6` floor is gone. The
   registered adapters are the orchestrator's `_search_adapters` keys
   (europepmc, pubmed, psyarxiv, socarxiv, biorxiv_medrxiv, arxiv = 6;
   crossref/unpaywall live in `_crossref`/`_unpaywall`, not the search map), so
   the exact 7 − 1 = 6 chain holds. Adding a source to any one list without the
   other two now fails — the exact-set assertions, not a floor, carry the guard.

3. GN6 — the connection test drops the id()-reuse flake without weakening.
   PASS. tests/test_db_concurrency.py:51 stores the connection objects in `seen`
   (keeping them alive) and compares with `is`:
   `seen["a"] is not seen["b"]` and `all(db.conn is not c for c in seen.values())`.
   The old `id()` comparisons could false-fail when a finished thread's
   connection was freed and its id reused. Object identity is strictly stronger
   than id equality — two distinct connections can never compare `is`-equal,
   and holding the objects prevents the garbage collection that caused the
   flake. The check still fails when connections are shared (both assertions
   go red on a single shared object). No weakening.

## Full suite

`venv/bin/python -m pytest tests/ -q` → **1954 passed, 2 skipped, 4 warnings
(0 failures)** in ~112s. The two skips are pre-existing conditional skips
(platform/node/font guards), unrelated to this diff. The retrieval-drift guard
(`tests/web/test_no_retrieval_drift.py`) is among the passing tests, confirming
no protected retrieval file changed in this range.

## TODO (below-medium; no code change required for this gate)

- GN4 test asserts behavioural equality on a finite input battery rather than
  proving single-source-of-truth by identity. The production code now genuinely
  delegates, so the battery (which includes the strip case, the one real past
  divergence) is the right regression pin; a future edit that re-introduces a
  near-copy identical on this battery but drifting on an untested input would
  still pass. Consider a direct identity assertion if the reader relationship
  is ever questioned again.
- The GN5 test imports the private `_SEARCH_SOURCES` symbol. Intentional (it
  ties the test to the producer constant, which is the whole point), but a
  later rename or make-public of that constant must update the test. Cosmetic.
- `SOURCES_WITHOUT_ADAPTER` is a tuple while `_SEARCH_SOURCES` is a list, for
  two names that mean "the list of sources". Cosmetic type mismatch; pick one.
- Pre-existing: the GN1/GN5 test's `section["enabled"] = True` loop mutates the
  config dict returned by `load_sources_config()`. Safe today because
  `sources_config.yaml` lists every source, so the merged `publication_sources`
  inner dicts are all fresh YAML-parsed copies; a source added to
  `_DEFAULT_CONFIG` without a matching YAML entry would let this test mutate the
  module-level default. Not introduced by this diff, but worth closing next time
  that file is touched.
