# Implementation plan — today's gate TODO items (2026-10-08, TG1–TG5)

**Request:** "fix the TODO items from today's gates".
**Status:** APPROVED 2026-10-08; built. TG1–TG5 done.
**Found during the build:**
- TG5: the KW7 test model already compared bare words as whole words.
  `texts` holds lists of words, so `in` tests list membership. The gate's
  note was mistaken. The planned cases were added and prove it: a
  substring version of the model fails `test_tg5_query_model_is_whole_word`.
- The route test for TG1 is in `tests/web/test_searches_routes.py` (it needs
  the web fixtures).
**Gate 1 (REJECTED, `docs/cycles/2026-10-08_gate-todos-qa-gate.md`) → TG6:**
- F1: `config_int` let `OverflowError` through, so `.inf` crashed searches.
- F2: the class was incomplete. Numeric settings were still read with bare
  `int()`/`float()` in several places.
- F3: a default above the ceiling reset both values to (200, 2000), throwing
  away a lowered ceiling.
- F4: the "agree" test never called the adapter.

| ID | Fix | Test |
|---|---|---|
| TG6 | One reader for every numeric setting, `src/config_values.py` (`config_int`, `config_float`). It refuses blank, text, fractions, bools, inf/nan and values below a minimum, and logs the full setting path. Every numeric read in both config files now uses it: (1) `orchestrator._page_limit` (protected); (2) `OsfPreprintAdapter._max_title_terms` (protected), so the adapter and the limit summary agree on any value (F4); (3) `fulltext.extract_limits`; (4) `paper_meta.scrape_max_chars`; (5) the summary routes' `max_downloads`; (6) `discover_settings`; (7) `llm_config` timeout, `max_text_chars` and the daily cap (0 is allowed for the cap). F3: a default above the ceiling is brought down to the ceiling. | `tests/test_config_values.py` (25: every bad shape for both readers, and each caller with inf); `tests/test_search_limits.py::test_tg1_default_above_ceiling_is_clamped`, `test_tg2_osf_terms_summary_and_adapter_agree` (calls the adapter); `test_orchestrator.py::test_br3_page_limit_bad_config_falls_back` message updated |

**Re-gate (APPROVED, `docs/cycles/2026-10-08_gate-todos-regate-qa-gate.md`):**
it found three `llm_config.yaml` reads still outside the reader and fixed
them, with tests: `sign_in_limits._setting` (F5); the `tokens.py` estimate
settings and rates (F6); `job_lanes` taking `true` as one worker (F7).
Reviewed before commit; the token high bound is unchanged for the shipped
config (12,000).

**Touches protected retrieval code (TG6, flagged):** `src/sources/orchestrator.py`
(`_page_limit`) and `src/sources/osf.py` (`_max_title_terms`). Each now reads its
setting through the shared reader; a value that was valid before reads the same.
The drift baseline is advanced.
**Source:** `TODO.md`, these sections:
- Limits and warnings gate
- Date-window gate
- Author-keywords gate

**Touches protected retrieval code:** none in TG1–TG5 (`biorxiv_window.py` is
not on the protected list). TG6 (after gate 1) changes `orchestrator.py` and
`osf.py`; see above.

---

## Items, decisions and tests

| ID | Item | Fix | Test |
|---|---|---|---|
| TG1 | **F1:** `search_limits` and `limit_summary._osf_narrowed` call bare `int()` on config. A blank (`null`), text or fractional value raises, and every search fails instead of falling back. | One helper, `config_int(block, key, default, minimum=1)`, in `src/search_limits.py`. It returns the value when it is a whole number ≥ minimum (bools refused). Otherwise it logs a warning naming the key and returns the default. If the default is above the ceiling, both fall back to (200, 2000), with a warning. | `tests/test_search_limits.py::test_tg1_*`: null, "", "lots", 2.5, True, 0, −3 and a default above the ceiling all fall back with a warning, and the search route still starts (400-free) with a broken config. |
| TG2 | **Same class, found while planning (P5):** `limit_summary` falls back to 8 OSF title terms, with its own literal instead of `osf.DEFAULT_MAX_TITLE_TERMS`. `biorxiv_window.window_settings` also calls bare `int()` (`max_direct_days`, `europepmc_lag_days`). | `_osf_narrowed` reads `osf.max_title_terms` through `config_int` with `osf.DEFAULT_MAX_TITLE_TERMS`. `window_settings` uses `config_int` with its existing defaults (21, 60). | `test_tg2_osf_terms_default_shared` (the summary and the adapter agree with the key missing or blank); `tests/test_biorxiv_window.py::test_tg2_bad_window_settings_fall_back` |
| TG3 | **Date-window L1:** "Searched all years (to END)" does not name the start date. | `dateWindowText` names it: "Searched all years (1900-01-01 to 2026-10-08)". Always shown, so a changed `search.all_years_start` is visible. | `tests/web/test_frontend_wiring.py::test_dw2_date_window_text` updated; `test_dw3_*` strings updated |
| TG4 | **Date-window L2:** `_ALL_YEARS_FALLBACK` and `_HINT_FALLBACK` repeat the values in `sources_config.yaml`. | Keep them as frozen defaults: they are what runs when the config is missing, so they cannot be read from it. Add a comment saying so, and a test that each fallback equals the shipped YAML value, so the two cannot drift apart unnoticed. The same goes for `_FALLBACK = (200, 2000)`. | `tests/test_search_limits.py::test_tg4_fallbacks_match_the_shipped_config` |
| TG5 | **Author-keywords note:** the KW7 test model treats a bare word as a substring, which is looser than Europe PMC. | Bare words match whole words only, as in Europe PMC. Add a bare-word case (`ketamine`) and an adversarial one: a bare `kin` must not be satisfied by "kinship". | `tests/test_query_builder.py::test_kw7_*` (new cases); `test_tg5_query_model_is_whole_word` |

**Not changed:** the second author-keywords note, "keyword matches count
against each source's limit". It needs no code: the limit summary already
reports it. It is removed from TODO.md with that reason.

## Build order

TG1 → TG2 (shared helper first) → TG4 → TG3 → TG5 → docs (CHANGELOG, spec
coverage, the three TODO sections removed) → full suite → `/chdp` Hermes gate
→ push → CI.

## Not in this plan

The 2026-10-07 hyphen-wildcard note (`OR*`) and older TODO sections are not
from today's gates; untouched.
