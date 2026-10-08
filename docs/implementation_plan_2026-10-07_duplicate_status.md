# Implementation plan — say when a source's papers were already found (2026-10-07, DS1–DS4)

**Request:** "fix the PubMed '0 fetched' status wording too" (after "why does it
seem to skip Euro PMC and Pubmed and go to PsyArxiv right away").
**Status:** APPROVED 2026-10-07; built. DS1–DS4 done.
**Touches protected retrieval code:** `src/sources/orchestrator.py` (W1.a) —
status text only, no change to what is fetched, merged or kept.

## 0. Today

After each source, `SourceOrchestrator.search` posts `"{label}: {fetched} fetched"`,
where `fetched` counts only records not already in the de-duplicator.
`_search_source` already counts the rest (`duplicates`), but only the
bioRxiv/medRxiv line mentions them. With Europe PMC and PubMed both ticked,
every PubMed record is already in Europe PMC (Europe PMC includes PubMed), so
the line reads "PubMed: 0 fetched" — live, `loneliness` 14 days: PubMed alone
finds 52, after Europe PMC "0 fetched". It reads as if PubMed was skipped.

## 1. Change

- `_search_source` keeps its return value and also leaves the source's
  duplicate count on the orchestrator (`self._last_duplicates`).
- A pure function `fetched_status(label, new, duplicates)` builds the line:

| new | duplicates | Line |
|---|---|---|
| N | 0 | `PubMed: N fetched` (unchanged) |
| 0 | D | `PubMed: D papers read, all already found by an earlier source` |
| N | D | `PubMed: N new, D already found by an earlier source` |
| 0 | 0 | `PubMed: 0 fetched` (unchanged — it found nothing) |

Added after the DS gate (F2) and TD9 — a source's own repeats are counted
apart from papers an earlier source found:

| new | already found | repeated | Line |
|---|---|---|---|
| N | 0 | R | `PubMed: N new, R repeated within PubMed` |
| N | A | R | `PubMed: N new, A already found by an earlier source, R repeated within PubMed` |

- bioRxiv/medRxiv keeps its own fuller line, which already names repeats.

## 2. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| DS1 | `fetched_status` gives the four lines above. | `tests/test_orchestrator.py::test_ds1_fetched_status` |
| DS2 | Two fake sources returning the same papers: the second's line says "all already found by an earlier source" with the right count; a partly overlapping second source says "N new, D already found". | `test_ds2_overlap_is_named` |
| DS3 | Records, counts and progress unchanged (the existing orchestrator and route tests pass unmodified). | full suite |
| DS4 | Drift baseline advanced with the reason; CI green. | `tests/web/test_no_retrieval_drift.py` |

Order: DS1 → DS2 → change → docs (CHANGELOG, TODO item removed) → suite →
`/chdp` Hermes gate → push → CI.
