# Implementation plan — wildcard on a hyphenated word (2026-10-07, HW1–HW3)

**Request:** "fix the hyphenated wildcard TODO too".
**Source:** `TODO.md`, gate follow-ups gate L1
(`docs/cycles/2026-10-07_gate-todos-qa-gate.md`).
**Status:** PLAN — awaiting owner approval. No code written.
**Touches protected retrieval code:** `src/sources/query_builder.py` (W1.a).

## 0. Today

`_term_clause` splits a phrase ending in `*` on spaces only. A single word
with punctuation (`COVID-1*`, `kin-select*`) needs quotes, and a quoted part
cannot keep a wildcard, so it is sent as the exact word `"COVID-1"`. The local
filter reads it as `covid 1*` (punctuation as space, TD4) and matches
"COVID-19". The query is narrower than the filter, so matches are never read.

## 1. Change

Split the stem of a wildcard part on the same characters the local filter
treats as spaces (whitespace and punctuation, `src/search_terms.py`), and send
the words as AND parts with the wildcard on the last:
`COVID-1*` → `(TITLE_ABS:COVID AND TITLE_ABS:1*)`,
`kin-select*` → `(TITLE_ABS:kin AND TITLE_ABS:select*)`. The same function
builds arXiv clauses. A superset of what the local filter keeps, which then
checks the exact form.

## 2. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| HW1 | Hyphenated and punctuated wildcard words are split into AND parts with the `*` on the last; plain wildcards (`adolescen*`) and spaced phrases (`kin select*`) unchanged. | `tests/test_query_builder.py::test_hw1_*` |
| HW2 | Superset (P7): for each case, every title the local filter keeps contains all the words the query sends. | `test_hw2_query_is_a_superset_of_the_filter` |
| HW3 | Live: `COVID-1*` in Europe PMC returns > 0 and the local filter keeps the COVID-19 papers among them (integration, status report); drift baseline advanced; CI green. | live check; `tests/web/test_no_retrieval_drift.py` |

Order: HW1/HW2 tests → change → live check → docs (CHANGELOG, TODO item
removed) → drift baseline → full suite → `/chdp` Hermes gate → push → CI.
