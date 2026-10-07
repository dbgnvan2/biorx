# Implementation plan — "all of these words" in one search box (2026-10-07, AND1–AND8)

**Request:** "if I put multiple terms in the 'title or abstract' box, separated
by a comma is that an AND search of those terms" → (no, OR) → "yes, plan the AND option".
**Status:** APPROVED 2026-10-07; built. AND1–AND8 done (tests in `docs/spec_coverage_webapp.md`); AND-L after deploy.
**Found while building:** `filtering.split_terms` lowercased terms, which would
have turned `AND` into `and`; it now keeps case and `term_matches` lowercases
each part. The page hints use `data-hint`, not `id` (a test forbids ids no
script reads). Affected local saved filter: "Bowen and Marriage".
**Adjacent, not fixed:** arXiv has no wildcards, so `cooperati*` is sent as
the non-word `cooperati` and finds 0 there (true before this change too).
**Touches protected retrieval code:** `src/sources/query_builder.py` (W1.a,
`tests/web/test_no_retrieval_drift.py`). This change is required to send the
new operator to the sources; it is flagged here and the drift baseline is
advanced in the same commit, with the reason in its comment.

---

## 0. Today

| Where | Meaning |
|---|---|
| commas in one box | OR |
| different boxes (Title, Abstract, Title-or-abstract) in a group | AND |
| groups in a filter | OR |
| several words in one term | exact phrase |
| `word*` | prefix of a word |

There is no way to require several words anywhere in the title or abstract.
`cooperative species survival` is one exact phrase (0 papers in Europe PMC),
while cooperative AND species AND survival finds 1,147 (90 days).

Two saved filters in the local database already contain ` AND ` (e.g. title
`Bowen AND Marriage`). Today that is searched as the literal phrase
"Bowen AND Marriage" and finds nothing.

## 1. The syntax

**` AND ` (uppercase, with a space each side) joins parts that must all
appear.** Commas still separate alternatives, and bind more loosely:

| Typed in one box | Means |
|---|---|
| `cooperati* AND survival` | both words (any order, anywhere in that field) |
| `cooperati* AND survival AND species` | all three |
| `kin selection AND survival` | the phrase "kin selection" and the word survival |
| `cooperati* AND survival, kin selection` | (cooperati* AND survival) OR "kin selection" |
| `anxiety and depression` (lowercase) | unchanged: one exact phrase |

Why uppercase `AND` and not `+`: it is what Europe PMC/Lucene and arXiv use,
it is what the owner already typed in a saved filter, and `+` appears inside
real terms ("CD4+ T cells"). Lowercase "and" is left alone because it occurs
in ordinary phrases. Empty parts (`AND survival`, `a AND AND b`) are dropped;
a term that is only `AND` is ignored.

**Behaviour change, flagged:** an existing filter with ` AND ` in a term
changes from "exact phrase, finds nothing" to "all parts". That is the
meaning the owner intended, but it is a change; the CHANGELOG says so, and
the status report lists every local saved filter it affects. Production
filters cannot be read from here.

## 2. Design

One parser, used everywhere: new module `src/search_terms.py` with
`and_parts(term) -> List[str]` (pure). `filtering.py`, `query_builder.py`
and `discover.py` all call it, so the sources, the local check and the
Discover counts cannot disagree.

| Consumer | Change |
|---|---|
| Local filter `src/filtering.py:text_group_matches` | a term matches when **every** part matches (`match_term` per part). This is what every source's results pass through, so it is the rule that decides what the user sees. |
| Europe PMC + PubMed `query_builder._group_to_lucene` | an AND term becomes `(TITLE:a AND TITLE:"b c")` (field prefix on each part; bare for Title-or-abstract) |
| arXiv `query_builder._group_to_arxiv` | `(ti:a AND ti:b)`, wildcards stripped per part as now |
| OSF / PsyArXiv / SocArXiv `query_builder.osf_title_terms` | an AND title term sends **one** part as `filter[title]` (the longest, after stripping `*`). Any paper containing all parts contains that one, so nothing is lost; the local filter removes the rest. |
| PsyArXiv/SocArXiv keyword string `build_psyarxiv_query` | parts added as separate keywords (the string is informational; OSF narrows by title) |
| bioRxiv/medRxiv | no change (date fetch + local filter) |
| Discover `count_term_hits` | an AND term counts a paper only when every part is there as whole words |
| Discover live check (DT9) | no code change: it already uses `build_europepmc_query` |
| Page | a short hint under each Title / Abstract / Title-or-abstract box and the Search box: "Commas = any of these. AND = all of these (e.g. cooperati* AND survival)." No change to how terms are stored. |

## 3. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| AND1 | `and_parts` splits on uppercase ` AND ` only; trims; drops empty parts; leaves lowercase "and" and "CD4+" alone. | `tests/test_search_terms.py::test_and1_*` (table of cases incl. `AND survival`, `a AND AND b`, `anxiety and depression`, `CD4+ T cells`) |
| AND2 | Local filter: every part must match. **Adversarial (P7):** a paper with "cooperation" but not "survival" fails `cooperati* AND survival`; one with both (any order, title + abstract) passes. Commas still OR: `a AND b, c` passes a paper with only c. | `tests/test_filtering.py::test_and2_all_parts_must_match`, `test_and2_comma_still_ors_and_terms`, `test_and2_title_field_needs_all_parts_in_title` |
| AND3 | Europe PMC query: exact expected strings for bare, TITLE:, ABSTRACT:, phrase part, wildcard part, mixed with commas, multiple groups. Live sanity: the query for `cooperative AND species AND survival` returns > 0 (flagged integration). | `tests/test_query_builder.py::test_and3_europepmc_*`; live check recorded in the status report, not in the suite |
| AND4 | arXiv query: `(all:a AND all:b)`, `ti:`/`abs:` per field, wildcard stripped per part. | `tests/test_query_builder.py::test_and4_arxiv_*` |
| AND5 | OSF: an AND title term sends exactly one part, the longest; superset property holds (any title containing all parts contains the sent part). Groups without title terms still fetch by date (existing B3 tests unchanged). | `tests/test_query_builder.py::test_and5_osf_sends_one_part_of_an_and_term`, existing `tests/test_adapters.py::test_b3_*` |
| AND6 | Discover counts: `cooperati* AND survival` counts a paper with both words, not one with only one. | `tests/web/test_discover_routes.py::test_and6_and_term_counts_need_every_part` |
| AND7 | Unchanged behaviour for terms without ` AND `: existing query-builder, filtering, adapter and Discover tests pass unmodified. | full suite |
| AND8 | Page hint present under every text box and the Search box, built with textContent; existing controls unchanged. | `tests/web/test_frontend_wiring.py::test_and8_and_hint_under_text_boxes` (reads `index.html`), existing `test_every_element_the_client_uses_exists_in_the_page` |
| AND-R | Retrieval-drift baseline advanced to this commit with a comment naming AND1–AND5 as the reason; CI green. | `tests/web/test_no_retrieval_drift.py` |
| AND-L | Live check on production: run a filter with Title-or-abstract `cooperati* AND survival` and record the count. | **Human/browser check** (live sources); needs a signed-in browser |

## 4. Build order

1. AND1 tests → `src/search_terms.py`.
2. AND2 tests → `filtering.py` (the rule users see; highest value).
3. AND3–AND5 tests → `query_builder.py`; advance drift baseline (AND-R).
4. AND6 → `discover.py`.
5. AND8 page hint.
6. Docs (CHANGELOG with the behaviour change, spec, coverage), full suite,
   `/chdp` Hermes gate, push, CI, list of affected local saved filters, AND-L.

## 5. Owner decisions (defaults chosen)

- **Operator:** uppercase ` AND `, not `+` (reasons in §1). This replaces the
  `+` I suggested in chat.
- **Lowercase "and"** stays part of a phrase.
- **Discover suggesting AND terms:** not in this plan. Once this ships, the
  Discover prompt could allow `a AND b` terms (the DT9 live check already
  handles them); a one-line config change, but it changes what the model
  returns, so it waits for a separate yes.

## 6. Adjacent issues found, not fixed

- `_lucene_term` escapes only `"`. A term containing `(`, `)`, `:` or `OR`
  goes into the Lucene query as syntax. Not new; not in this plan.
- No NOT operator. Not requested.
