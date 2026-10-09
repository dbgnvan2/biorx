# QA gate — the unrecorded notes from today's gates (2026-10-08, GN1–GN3)

RANGE:   origin/main...HEAD (3 commits)
COMMITS: eec1d26 docs: plan GN1-GN3 - the unrecorded notes from today's gates
         05a5a89 fix: the unrecorded notes from today's gates (GN1-GN3)
         de795c1 test: advance the retrieval-drift baseline to the GN commit

Plan: docs/implementation_plan_2026-10-08_gate_notes.md
Protected path touched: src/sources/schema.py (GN3 helper), src/sources/europepmc.py
and osf.py (GN2/GN3), src/sources/dedup.py (GN2) — flagged W1.a in the plan. Drift
baseline advanced once, 05a5a89, with the reason named in test_no_retrieval_drift.py;
the reachability guard (test_the_baseline_commit_is_reachable) is intact.

Reviewer: learning-qa — one verification pass by the gate runner over the
materialized diff (origin/main...HEAD), tracing the two named gate checks against
the real producers (the orchestrator's registration path and the filter's matcher).

## Verdict: APPROVED

Both named gate checks pass, the full suite is green, and no MEDIUM-or-higher
finding remains. The change is exactly the two below-medium notes the KW gate
(docs/cycles/2026-10-08_author-keywords-qa-gate.md) filed to the backlog, closed
with a shared helper and a classification guard. No fix commit was introduced, so
the loop's stopping condition (no finding of MEDIUM-or-higher requiring a code
change) is met. All remaining notes are below-medium and go to TODO.

---

## The two named gate checks

### 1. keyword_list handles every shape (P7) — PASS

`keyword_list` (schema.py:163) is total over every shape a source can send:

- str → one-item list (the P7 case: `"Internal Family Systems"` is ONE keyword,
  not 23 letters); empty/whitespace string → `[]`.
- list/tuple → non-blank strings, each stripped; non-str elements (None, int,
  dict, nested list) dropped without error.
- anything else (None, int, bool, dict) → `[]`.

The two call sites guard their own raw shapes before delegating:

- europepmc.py:307-308 wraps `raw.get("keywordList")` in `isinstance(kw_block,
  dict)`, so a `keywordList` that is None, a string, or a list (all of which the
  OLD `raw.get("keywordList", {}).get("keyword", [])` would have crashed on with
  `AttributeError`) reads as no keywords. This is strictly more robust than before.
- osf.py:224 `tags = attrs.get("tags", []) or []` then `keyword_list(tags)`; a
  single-string `tags` reaches the helper as one item, a dict reaches it as `[]`.

The adversarial test (`test_gn3_single_string_keyword_is_one_keyword`) pins the
one-string case for BOTH adapters plus the odd shapes (None, int, dict, and a
`keywordList` block that is None/string/list). No sibling adapter was missed:
only europepmc and osf populate keywords (biorxiv_medrxiv, arxiv, crossref set
`keywords=[]`; pubmed/psyarxiv/socarxiv inherit via their parents).

### 2. limit_summary classification guard is real, built from the real orchestrator — PASS

`test_gn1_every_registered_source_is_classified` imports the real
`SourceOrchestrator`, `load_sources_config()`, enables every source, and iterates
`orch._search_adapters` — the registration the orchestrator actually builds — then
asserts an exact bijection, not a floor:

    for name in registered:
        assert sum(name in group for group in lists) == 1   # exactly one class
    assert set().union(*lists) == registered                 # no extras, none missing

So the guard derives from the producer's real code (Pitfall 2), and it is exact
(P29-safe): adding an unclassified source to the orchestrator, or leaving a stale
name in a list, both go red. The runtime fallback is also real and tested: an
unclassified source logs a warning and gets the honest "papers" wording
(`test_gn1_unknown_source_is_not_mislabelled`), and `biorxiv_medrxiv` reads
"papers in the date range" through the new explicit `DATE_SOURCES` branch.

The six registered search sources (europepmc, pubmed, psyarxiv, socarxiv,
biorxiv_medrxiv, arxiv) map 3+2+1 into WORD/OSF/DATE. crossref/unpaywall are
excluded correctly — they register into `_crossref`/`_unpaywall`, not
`_search_adapters`.

### Nothing else changed in what is fetched or kept — PASS

The diff touches only `normalize()` keyword handling (both adapters),
`merged_keywords` (stored text), and `limit_summary` wording. No `search()` /
paging / query-builder / filtering change. Matching is unaffected by the strip:
`filtering.term_matches` normalises every field through `normalise_text` before
`match_term`, so a stored keyword's surrounding whitespace was already irrelevant;
`dedup.merged_keywords` still dedups on the same stripped/lowered key and now
stores the stripped form. `limit_summary._counts` returns identical wording for all
six known sources; only the previously-wrong unknown-source label changed.

## Full suite

venv/bin/python -m pytest tests/ -q  →  1953 passed, 2 skipped, 4 warnings

The 2 skips are the pre-existing environment/CI skips (RLIMIT_AS Linux-only;
Python version check). All GN1–GN3 tests pass, and the advanced-baseline drift
tests (test_the_protected_files_all_exist, test_retrieval_modules_unchanged_since_the_web_work_began,
test_the_baseline_commit_is_reachable, test_the_guard_would_notice_a_change) pass.

## TODO (below-medium notes — no code change made)

1. keyword_fields (filtering.py:193) is a hand-maintained near-copy of the new
   keyword_list (schema.py:163): same str→list / non-list→[] shape handling, but it
   returns `k` unstripped where keyword_list returns `k.strip()`. They agree on
   every shape today and the strip difference is masked by normalise_text, so no
   bug — but this is exactly the parallel-copy drift shape (Pitfall 2 / P19). Worth
   sharing the helper so producer and consumer normalise from one function.

2. Three hand-maintained source lists still exist: config._SEARCH_SOURCES (7,
   incl. openalex), orchestrator._register_adapters (6 hardcoded blocks), and
   limit_summary's WORD/OSF/DATE (6). The GN1 guard now binds #2↔#3, but
   _SEARCH_SOURCES remains a separate list. openalex is declared a search source
   with no search adapter (pre-existing, documented at test_orchestrator.py:734);
   if it is ever wired, the GN1 guard will correctly fail until it is classified.
   Not a defect in this change; a consolidation is a candidate follow-up.

3. `assert len(registered) >= 6` in the GN1 test is a floor (P29), but it is
   subordinate to the exact `union == registered` and `sum == 1` assertions, which
   are the actual guard — the floor is a friendlier pre-check, not the protection.

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P2, P4, P5, P7, P19,
P21, P26, P29, P35, and Pitfall 2 (derive checks from the producer's real code).
Traced: src/sources/schema.py (keyword_list), europepmc.py:304-358, osf.py:224-256,
dedup.py:115-125, limit_summary.py:25-100, filtering.py:119-138/193-208, the
orchestrator's _register_adapters (orchestrator.py:94-124), sources/config.py
(_SEARCH_SOURCES, is_source_enabled), sources_config.yaml, and the four test files
changed.

Not covered: live source responses for a real keyword-only search (the KW gate's
live check stands); logic/algorithmic correctness, concurrency, authn/authz,
performance, and dependency risk are outside the learning-qa family.
