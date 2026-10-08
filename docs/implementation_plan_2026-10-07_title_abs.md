# Implementation plan — Europe PMC searches title and abstract only (2026-10-07, TA1–TA6)

**Found:** 2026-10-07 while checking search-within in a local browser:
"Found 200 · Matched 16" for `cooperati* AND survival` (Europe PMC, 14 days).
**Status:** APPROVED 2026-10-07; built. TA1, TA2, TA4, TA5 done; TA3 partial (see coverage: one term at 80%, a hyphen case in the local filter); TA6 after deploy.
**Change from the plan:** the term button still reads "N in Europe PMC" (space);
its tooltip, the dropped-term reason and the all-dropped message say
"title or abstract".
**Touches protected retrieval code:** `src/sources/query_builder.py` (W1.a);
flagged here, drift baseline advanced in the same batch.

---

## 0. The defect

A Title-or-abstract ("both") term is sent to Europe PMC and PubMed as a bare
term (`query_builder._group_to_lucene`, comment: "bare term matches title OR
abstract in Europe PMC"). That comment is wrong: a bare term matches **every
field, including full text**. The app then keeps only title/abstract matches
(`src/filtering.py`), after reading at most Max results (default 200) of what
Europe PMC returned.

Europe PMC hit counts, 2026-10-07:

| Term | Window | Bare (sent today) | `TITLE_ABS:` |
|---|---|---|---|
| cooperati* AND survival | 14 days | 460 | 17 |
| adolescen* | 90 days | 25,411 | 7,831 |
| loneliness | 90 days | 2,391 | 731 |
| "kin selection" | 90 days | 28 | 5 |
| "cooperative breeding" | 90 days | 19 | 5 |

Consequences:
1. **Searches miss papers.** The 200 records read are mostly full-text-only
   matches, which the local filter then drops. Title/abstract matches beyond
   the first 200 are never read. "Found 200 · Matched 16" with 17 real matches
   is this.
2. **Searches are slow for nothing:** most records fetched (and paged
   through) are thrown away.
3. **The Discover check (DT9) overcounts.** It uses the same query, so a term
   can show "19 in Europe PMC" and find 5 (or 0) after filtering — it can still
   offer a term that shows nothing.

Title-only and abstract-only boxes already use `TITLE:` / `ABSTRACT:` and are
not affected.

## 1. Fix

Send Title-or-abstract terms with Europe PMC's `TITLE_ABS:` field, the same
way `TITLE:` and `ABSTRACT:` are sent: `TITLE_ABS:loneliness`,
`TITLE_ABS:"kin selection"`, `TITLE_ABS:adolescen*`,
`(TITLE_ABS:cooperati* AND TITLE_ABS:survival)`. One change in
`_group_to_lucene` (`_lucene_clause(t, "TITLE_ABS")`). PubMed uses the same
builder (plus `SRC:MED`) and gets the fix too. The Discover check uses
`build_europepmc_query`, so its counts become title/abstract counts with no
change to `src/discover.py`; the chip text becomes "N in Europe PMC titles
and abstracts".

## 2. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| TA1 | Every "both" term is sent as `TITLE_ABS:`: single word, phrase, wildcard, AND parts, comma alternatives, several groups. No bare term remains for "both". | `tests/test_query_builder.py::test_ta1_*`; existing tests that asserted bare terms are updated (listed in the commit — the old expectation was the defect) |
| TA2 | Title-only / Abstract-only queries unchanged. | existing `test_single_title_term`, `test_single_abstract_term`, AND3 title/abstract cases |
| TA3 | Adversarial (P7): the local filter and the query now agree on the field — a record whose only match is in full text would not be requested. Checked offline by asserting the clause field, and live (integration, recorded in the status report): `loneliness` 90 days returns ≈731, and the share of fetched records kept by the local filter rises from ~30% to > 90% on three sample terms. | `test_ta3_both_terms_never_bare`; live check in the status report (not in the suite) |
| TA4 | Discover's check query contains `TITLE_ABS:`; chip text and `dropped` reason say "titles and abstracts". | `tests/web/test_discover_routes.py::test_ta4_check_counts_titles_and_abstracts`; `tests/web/test_frontend_wiring.py::test_ta4_*` |
| TA5 | Retrieval-drift baseline advanced with the reason; CI green. | `tests/web/test_no_retrieval_drift.py` |
| TA6 | Live on production: a "loneliness" search (Europe PMC, 14 days) shows Found ≈ Matched instead of 200 → a fraction. | **Human/browser check** |

## 3. Build order

TA1/TA3 tests → `_group_to_lucene` → update old bare-term expectations →
TA4 → docs → full suite → `/chdp` (Hermes gate) → push → CI → TA6.

## 4. Not in this plan

- `_lucene_term` escaping of reserved words/characters (TODO, AND gate F1).
- arXiv `all:` also matches authors and comments (already documented,
  filtered locally; arXiv has no title-or-abstract field).
