# Implementation plan — today's gate TODO items (2026-10-07, TD1–TD11)

**Request:** "fix the TODO items from today's gates".
**Status:** PLAN — awaiting owner approval. No code written.
**Touches protected retrieval code:** `src/sources/query_builder.py` (TD1, TD3),
`src/sources/orchestrator.py` (TD8, TD9), `src/sources/biorxiv_medrxiv.py`
(TD6, TD7) — W1.a, flagged here; drift baseline advanced once for the batch.

Source of each item: `TODO.md`, sections added 2026-10-07.

---

## 1. Items, decisions and tests

### Search terms (AND-terms gate, TITLE_ABS, search-within gate)

| ID | Item | Fix | Test |
|---|---|---|---|
| TD1 | `_lucene_term` escapes only `"`: a part such as `OR`/`NOT`, or containing `: ( ) [ ] { } ^ ~ ? \ / + - ! &`, reaches Europe PMC as query syntax while the local filter reads it literally. | One helper, `_needs_quotes(part)`: a part is quoted (with `\` and `"` escaped) when it contains a space, any of those characters, or is exactly `AND`/`OR`/`NOT`. A trailing `*` stays a wildcard only on an unquoted part; a part that must be quoted loses the `*` (logged). Applied to Europe PMC and arXiv clauses. | `tests/test_query_builder.py::test_td1_*`: `COVID-19: outcomes` → `TITLE_ABS:"COVID-19: outcomes"`; part `OR` → `TITLE_ABS:"OR"`; `(x)` quoted; `adolescen*` unchanged; adversarial (P7): a term built to inject `) OR (TITLE_ABS:*` stays inside one quoted clause. Live: `TITLE_ABS:"COVID-19: outcomes"` accepted by Europe PMC (integration, status report). |
| TD2 | `src/search_terms.is_and_term` has no callers. | Delete it. | `tests/test_search_terms.py` still passes; grep in the status report. |
| TD3 | arXiv: `cooperati*` is sent as the non-word `cooperati` (0 results). Live 2026-10-07, last year: `all:cooperati` 0, `all:cooperati*` 50, `all:cooperation` 3,273. arXiv's own wildcard is partial but better than none. | Send the `*` to arXiv instead of stripping it. Inside an AND term, if at least one part has no wildcard, leave the wildcard parts out of the arXiv clause (a superset; the local filter checks every part). | `test_td3_arxiv_keeps_wildcard`, `test_td3_arxiv_and_term_drops_wildcard_parts_when_others_remain` |
| TD4 | Hyphens: "kin selection" does not match "kin-selection" locally; Europe PMC does (TA3 live check, 4 of 5 kept). | Matching treats `-`, `‐`, `–` as a space in both the text and the term (one function in `src/search_terms.py`, used by `filtering.match_term` and Discover's counts). | `tests/test_filtering.py::test_td4_hyphen_matches_space` (both directions; `CD4+` and `COVID-19` still match themselves); Discover count test |
| TD5 | A phrase can match across the title/abstract join ("…cortisol" + "Sleep…" = "cortisol sleep"). | `term_matches(term, *fields)`: each AND part must match **within one field**; different parts may be in different fields (as now). Used by the Title-or-abstract box, search-within and Discover's counts. | `test_td5_phrase_does_not_span_title_and_abstract` (adversarial); existing `test_and2_all_parts_must_match` (parts in different fields) still passes |

### bioRxiv/medRxiv (window plan, window gate)

| ID | Item | Fix | Test |
|---|---|---|---|
| TD6 | `BiorxivMedrxivAdapter._servers()` reads `biorxiv_medrxiv.servers` from the config's top level, not `publication_sources`, so the setting is never read. | Read `publication_sources.biorxiv_medrxiv.servers` (the shape the orchestrator passes). | `tests/test_adapters.py::test_b7_servers_configurable` updated to the real config shape (it used the wrong shape, which is why the bug was not caught); plus a test with `load_sources_config()`-shaped input |
| TD7 | Gate L1: the job note uses today's date when the search starts; the adapter checks again on each page. Across midnight the note could be a day off, and the window could move between pages. | The search route fixes the search's dates once (`start_date`/`end_date` from `get_date_range`) before the note and the search, so every source and the note use the same window; the adapter decides its window on page 1 and keeps it for the search. | `tests/web/test_searches_routes.py::test_td7_dates_fixed_once`; `tests/test_adapters.py::test_td7_window_kept_across_pages` (patched `_today` changes between page 1 and 2 → same dates sent) |
| TD8 | Gate L2: the route and monitor call `orchestrator._resolve_active_sources`, a private method. | Rename to `resolve_active_sources` (public), keep the old name as an alias for one release. | existing BW5 tests updated to the public name; `test_td8_old_name_still_works` |

### Duplicate status (DS re-gate)

| ID | Item | Fix | Test |
|---|---|---|---|
| TD9 | A source re-sending a paper it already sent, after another source merged into it, is counted as "already found by an earlier source". | Track the records this source has already sent (by object identity — a merge updates the record in place); a paper sent again is a repeat, whatever else merged into it. | `tests/test_orchestrator.py::test_td9_resend_after_merge_is_a_repeat` (Europe PMC sends X; PubMed sends X twice → 1 already found, 1 repeated) |
| TD10 | `test_ds2_counts_are_not_shared_between_concurrent_searches` partly checks source text. | Replace with a two-thread run: two searches on one orchestrator, released in an interleaving order by events, each with a different overlap; each search's PubMed line has its own numbers. | the replaced test, mutation-checked against a shared attribute |
| TD11 | The DS1 table in the duplicate-status plan lists four lines; there are six. | Update the table in `docs/implementation_plan_2026-10-07_duplicate_status.md`. | doc diff |

## 2. Build order

TD2 → TD4 → TD5 (shared matching, highest user impact) → TD1 → TD3
(query builder) → TD6 → TD7 → TD8 (bioRxiv/route) → TD9 → TD10 (orchestrator)
→ TD11 → drift baseline → docs (CHANGELOG, remove the TODO sections) → full
suite → live checks (TD1 Europe PMC, TD3 arXiv, TD4 "kin selection") →
`/chdp` Hermes gate → push → CI.

## 3. Behaviour changes to call out

- TD4: hyphenated and spaced forms now match each other locally (more
  matches, consistent with Europe PMC).
- TD5: a phrase split across title and abstract no longer matches (fewer,
  correct matches).
- TD3: arXiv wildcard terms return some results instead of none.
- TD7: all sources in one search use one fixed date window.

## 4. Not in this plan

Older TODO sections (before 2026-10-07) are untouched.
