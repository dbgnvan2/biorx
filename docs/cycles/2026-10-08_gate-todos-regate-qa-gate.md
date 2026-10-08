# QA re-gate — today's gate TODO items (TG1–TG6, fix loop 1)

**Verdict: APPROVED**

RANGE:       origin/main..HEAD (4 commits: 2728785 plan, 81033ad TG1–TG5,
             e3a0cfe TG6 gate-1 fixes, 04c2dac baseline) + fix-loop-1
             working-tree changes (`src/sign_in_limits.py`, `src/tokens.py`,
             `src/llm_config.py`, and their tests)
APPLICABLE:  P4, P5, P19, P32
CHECKED:     P4, P5, P19, P32 (plus P9/P24 incidentally)
NOT COVERED: no caller-excluded paths. Scope-limits families not assessed:
             authn/z, injection, concurrency, performance, dependency risk.

## Gate-1 findings (F1–F4) — verified fixed, each with a test

- **F1** `config_int` let `OverflowError` through on `.inf`. Fixed:
  `src/config_values.py` now catches `(TypeError, ValueError, OverflowError)`.
  Test: `tests/test_config_values.py::test_tg6_config_int_never_raises`
  (parametrised with `inf`, `-inf`, `nan`; also `config_float`).
- **F2** sibling `int()` reads (`orchestrator._page_limit`,
  `fulltext.extract_limits`, `paper_meta.scrape_max_chars`). All three now
  read through `config_int`/`config_float`.
  Test: `tests/test_config_values.py::test_tg6_every_numeric_setting_survives_a_bad_value`.
- **F3** a lowered ceiling was reset to `(200, 2000)`. Fixed:
  `src/search_limits.py` clamps the default down to the ceiling.
  Test: `tests/test_search_limits.py::test_tg1_default_above_ceiling_is_clamped`
  (covers the valid-low-ceiling case `200 default / 100 ceiling → (100, 100)`).
- **F4** the "agree" test never invoked the adapter. Fixed: it now calls
  `PsyArxivAdapter._max_title_terms()` and compares against
  `_osf_narrowed` over the same values.
  Test: `tests/test_search_limits.py::test_tg2_osf_terms_summary_and_adapter_agree`.

## Re-sweep findings (fix loop 1) — the class was still incomplete

The plan's TG6 claim "every numeric read in both config files now uses it"
was false: three `llm_config.yaml` reads still bypassed the reader.

- **F5** `src/sign_in_limits.py::_setting` read `sign_in.attempts_per_minute`
  / `max_concurrent` with bare `int()` catching only `(TypeError, ValueError)`
  — `int(float('inf'))` raised `OverflowError` on the sign-in path (probed:
  `from_config({"sign_in": {"attempts_per_minute": float("inf")}})` raised),
  and a fraction `2.5` truncated to `2` / a bool became `1`. Fixed: routed
  through `config_int(…, minimum=1)`. Test:
  `tests/web/test_auth.py::test_t15c_bad_sign_in_settings_fall_back`
  (added `inf` and `2.5`/`True` cases).
- **F6** `src/tokens.py::_setting` accepted `inf`/`bool` (`value >= 0` passes
  for `inf`), then `int(inf)` raised `OverflowError` in
  `estimate_summary_tokens`; the top-level `max_text_chars` hard cap was also
  read raw (`.inf` or a string crashed). Probed: five `token_estimate` fields
  and `max_text_chars` each crashed on `.inf`. Fixed: `_setting` rejects
  bool/non-finite; `rate_for` rejects bool/`inf`/`nan` prices; the high bound
  now reads `llm_config.max_text_chars` (single source). Tests:
  `tests/test_tokens.py::test_tg6_inf_or_bool_estimate_setting_falls_back`,
  `tests/test_tokens.py::test_m5a1_a_malformed_rate_is_no_rate` (added
  bool/inf/nan rate cases).
- **F7** `src/llm_config.py::job_lanes` accepted a bool as a lane count
  (`search_workers: true` → `{'search': True}`, then `int(True) == 1`), unlike
  its sibling `job_max_unfinished` which already rejected bool. Fixed: rejects
  bool. Test: `tests/web/test_jobs.py::test_a14_lanes_from_config`.

## Verification (all real runs)

- Full suite: `venv/bin/python -m pytest tests/ -q` → **1948 passed, 2 skipped**.
- Probes: `sign_in` `.inf`/`-inf`/`2.5`/`True` now fall back to (20, 4);
  all six token-estimate fields and the top-level `max_text_chars` fall back
  to the default estimate instead of raising; `job_lanes(True)` returns `{}`.
- Remaining `int()`/`float()` calls in `src/` were audited: the only ones left
  are non-config (SQLite row ids, argv, HTTP headers, request params,
  arithmetic) or environment variables (`int("inf")` → `ValueError`, already
  caught; not a config file). No YAML numeric read bypasses the reader.
- F4 differential check: the summary and adapter now share one reader
  (`config_int`) and agree on `2.5`, `True`, `0` (both fall back), not just
  missing/blank/text.

## Verdict

APPROVED — gate-1 findings F1–F4 are fixed and covered by exact-value tests;
fix loop 1 closed the three remaining bypasses the re-sweep found (F5–F7),
each with a regression test; the full suite is green. The fix-loop-1 changes
are in the working tree (uncommitted).
