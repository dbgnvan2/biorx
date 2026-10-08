# QA gate — today's gate TODO items (TG1–TG5)

**Verdict: REJECTED** (3 medium findings; shipped config unaffected, fixes are small)

RANGE:       origin/main..HEAD (2 commits: 2728785 plan, 81033ad fix)
APPLICABLE:  P4, P5, P19, P32
CHECKED:     P4, P5, P19, P32 (plus P9/P24 incidentally)
NOT COVERED: no caller-excluded paths. Scope-limits families not assessed:
             authn/z, injection, concurrency, performance, dependency risk.

## Verification (all real runs)

- Full suite: `venv/bin/python -m pytest tests/ -q` → **1910 passed, 2 skipped**.
- Protected retrieval file: **none changed.** `src/sources/biorxiv_window.py`
  is not on the protected list (`biorxiv_medrxiv.py` is, and is untouched);
  `src/sources/osf.py` is only imported-from, not modified.
- TG5 claim (KW7 model already whole-word): **VERIFIED TRUE.** `_matches_query`'s
  bare-word branch is `any(value.lower() in w for w in texts)` where `texts` is a
  list of *lists of words*, so `in` is list membership, i.e. whole-word — not
  substring. The gate note was mistaken; `test_tg5_query_model_is_whole_word`
  correctly locks it (`"kin"` is not satisfied by `"kinship"`).

## Targeted checks (as requested)

1. config_int falls back on every bad shape — **NO.** `int(float('inf'))` raises
   `OverflowError`, which is outside the `except (TypeError, ValueError)`; probed,
   `config_int({"k": float('inf')}, 'k', 8)` raises. YAML parses
   `max_results_ceiling: .inf` as `float('inf')`, so one bad value still crashes
   every search — the exact failure TG1 exists to prevent. (NaN, fraction, bool,
   0, negative, blank, text, huge int all fall back correctly.)
2. All sibling numeric config reads covered — **NO.** `orchestrator._page_limit`
   (`src/sources/orchestrator.py:367`, on the search path) and
   `fulltext.py:334/337` + `paper_meta.py:176` still read `sources_config.yaml`
   numerics with bare `int()`; none catch `OverflowError`, and fulltext's two are
   entirely unguarded. TG2 invoked P5 but enumerated only `biorxiv_window`.
3. search_limits default-above-ceiling — **works as coded, but reverses a valid
   ceiling.** `search_limits({"search":{"max_results_ceiling":100}})` with the
   shipped `default_max_results: 200` returns `(200, 2000)`: a user who lowers the
   ceiling to 100 gets it silently reset to 2000 (a behaviour change from the old
   `(200, 100)`). The test only covers the both-wrong case `(900, 500)`.
4. Limit summary and OSF adapter agree — **only for missing/blank/text.** They
   diverge on fraction/bool/zero: the adapter `int(2.5)=2` / `int(True)=1` / 0,
   while the summary falls back to 8. This is documented and deferred in the plan
   (protected `osf.py`), but the test claiming "agree" never invokes the adapter
   and cannot detect the divergence (see F4).
5. TG5 claim — **TRUE** (above).

## Findings (ranked)

F1 · src/search_limits.py:44 · `config_int` raises `OverflowError` on `float('inf')`/
    `-inf`, so the "bad value must not crash searches" contract is false for a
    parseable YAML value · catch `(TypeError, ValueError, OverflowError)` ·
    confidence: high (probed)

F2 · src/sources/orchestrator.py:367; src/fulltext.py:334,337; src/paper_meta.py:176 ·
    P5 class incomplete — sibling `sources_config.yaml` numeric reads still use bare
    `int()` (search-path `max_pages`; unguarded `full_text.max_pages`/
    `extract_memory_mb`; loosely-guarded `scrape_max_chars`), none catching
    OverflowError · route them through `config_int` (or a try/except covering
    OverflowError) or scope the TODO explicitly · confidence: high (grep)

F3 · src/search_limits.py:59-63 · default-above-ceiling resets both to frozen
    `(200, 2000)`, silently discarding a valid lowered ceiling (ceiling=100 +
    default=200 → returns (200, 2000)) · clamp the default to the ceiling instead of
    resetting both, and add a regression test for the valid-low-ceiling case ·
    confidence: high (behaviour confirmed by inspection)

F4 · tests/test_search_limits.py:329-341 · `test_tg2_osf_terms_default_shared`
    docstring/name claims the summary and adapter "agree", but they demonstrably do
    not for `2.5` (in the parametrize list), and the test never calls
    `OsfPreprintAdapter._max_title_terms`, so it asserts only one side (P32/P19) ·
    rename to "summary falls back to the default" and drop the agreement claim, or
    assert the adapter side too · confidence: high

## Verdict

REJECTED — 3 medium findings (F1, F2, F3), 1 low (F4). None affects the shipped
`sources_config.yaml`; all are small fixes. TG1–TG5 otherwise implemented and
tested, suite green, TG5 claim confirmed.
