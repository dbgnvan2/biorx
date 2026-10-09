# Implementation plan — the unrecorded notes from today's gates (2026-10-08, GN1–GN3)

**Request:** "fix the TODO items from today's gates" (second time).
**Status:** APPROVED 2026-10-08; built. GN1–GN3 done (each test mutation-checked).
**Source:** `TODO.md` has no open items from today's gates. Four notes were
listed only as "below-medium, backlog" in the gate files and never reached
TODO.md:
- `docs/cycles/2026-10-08_limits-and-warnings-qa-gate.md`, "Notes":
  1. `_osf_narrowed` hard-coded 8 — **already fixed** (TG2/TG6: shared reader
     with `osf.DEFAULT_MAX_TITLE_TERMS`);
  2. the hand-kept source lists in `limit_summary` — **GN1**.
- `docs/cycles/2026-10-08_author-keywords-qa-gate.md`, F2 and "Notes":
  3. keyword matches using up the limit (F2) — **already closed** (the limit
     summary reports it; recorded in TODO.md);
  4. keywords stored unstripped — **GN2**;
  5. a keyword field given as one string would be split into letters — **GN3**.

**Touches protected retrieval code (W1.a, flagged):** `src/sources/europepmc.py`,
`src/sources/osf.py`, `src/sources/dedup.py` (GN2, GN3). The drift baseline
is advanced once.

---

## Items, fixes and tests

| ID | Item | Fix | Test |
|---|---|---|---|
| GN1 | `limit_summary.WORD_SOURCES` / `OSF_SOURCES` must match the orchestrator's sources by hand. A new source missing from both would be described wrongly in the summary ("papers in the date range" for a word search). | Keep the lists, and add a guard. Every source the orchestrator registers must be in exactly one of `WORD_SOURCES`, `OSF_SOURCES` or `DATE_SOURCES` (new: `biorxiv_medrxiv`). `_counts` uses `DATE_SOURCES` explicitly. An unknown source logs a warning and gets the plain "papers read" wording, not a wrong label. | `tests/test_limit_summary.py::test_gn1_every_registered_source_is_classified` (built from a real `SourceOrchestrator` with every source enabled); `test_gn1_unknown_source_is_not_mislabelled` |
| GN2 | Keywords keep surrounding spaces: the adapters keep `k`, not `k.strip()`, and `merged_keywords` appends the unstripped value. Matching is unaffected, but stored and shown keywords can carry stray spaces. | The adapters store `k.strip()`. `merged_keywords` appends the stripped value. *Protected.* | `tests/test_adapters.py::test_gn2_keywords_stripped` (both adapters); `tests/test_dedup.py::test_gn2_merged_keywords_stripped` |
| GN3 | A `keywordList.keyword` or `tags` value that is a single string, not a list, would be iterated letter by letter into one-letter keywords. This was there before KW (the old `list(...)` had the same flaw). | One helper, `keyword_list(value)`, in `src/sources/schema.py`: a list gives its non-blank strings, stripped; a single string gives a one-item list; anything else gives an empty list. Used by both adapters. *Protected.* | `tests/test_adapters.py::test_gn3_single_string_keyword_is_one_keyword` (Europe PMC and OSF; adversarial P7: `"Internal Family Systems"` must not become 23 letters) |

## Build order

GN3 (the helper) → GN2 (strip, using the helper) → GN1 → docs (CHANGELOG,
spec coverage; TODO.md lists the notes as fixed) → drift baseline → full
suite → `/chdp` Hermes gate → push → CI.

## Process note

Gate notes marked "backlog" will be copied into TODO.md when each gate is
committed, so none is left only in a gate file.
