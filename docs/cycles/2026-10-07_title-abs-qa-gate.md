# QA Gate — Europe PMC TITLE_ABS for title-or-abstract terms (2026-10-07, TA1–TA6)

**RANGE:** `git diff origin/main...HEAD` (base `78bff46`)
**COMMITS:** b384965 (fix), c276de1 (drift baseline)
**Plan:** `docs/implementation_plan_2026-10-07_title_abs.md`
**Reviewer:** learning-qa failure-pattern sweep (cold, self-contained — read the
diff and traced the contracts through the modules, did not see how the code was written)
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1730 passed, 2 skipped** (green)

## Verdict: APPROVED

All four gate checks pass, the full suite is green, and no finding rises to
HIGH or blocking MEDIUM. Two LOW findings are recorded for the backlog; neither
is introduced by this change — one is a pre-existing filter/phrase edge the
change *surfaced* and logged, the other is test-clarity.

---

## APPLICABLE

P2 (silent drop — full-text matches silently read then discarded), P5 (sibling
calls — PubMed shares the query path), P7 (adversarial — "no bare search word"),
P19 (producer/consumer drift — query builder vs local filter vs Discover),
P26 (fix commit — the baseline advance is itself a retrieval edit that must be
re-swept), P28 (test→real-artifact isolation — the drift baseline guard),
P29 (floor vs exact assertions — test expectations must pin exact strings, not
`>=`).

## CHECKED

### 1. Europe PMC/PubMed query and the local filter agree on fields — PASS

The single change is `_group_to_lucene` (src/sources/query_builder.py:84): the
"both" (title-or-abstract) field now sends `_lucene_clause(t, "TITLE_ABS")`
instead of a bare term. `TITLE_ABS` is Europe PMC's "title OR abstract" field —
exactly what the local filter's "both" branch matches against
(`text_group_matches`, src/filtering.py:203, `f"{title} {abstract}"`). There is
no second copy of the field decision:

- `title` → `TITLE:` (query_builder.py:72) — unchanged
- `abstract` → `ABSTRACT:` (query_builder.py:76) — unchanged
- `both` → `TITLE_ABS:` (query_builder.py:84) — was bare, now field-qualified

PubMed shares this path end to end: `orchestrator._build_query` routes both
`europepmc` and `pubmed` to `build_europepmc_query` (orchestrator.py:311-312),
and `PubMedAdapter` subclasses `EuropePmcAdapter` (pubmed.py:13), only appending
`SRC:MED`. One producer, no parallel hand-copy (P19, pitfall 2). Verified the
diff touches only `_group_to_lucene`; no other builder in the protected set
changed.

### 2. Title-only and Abstract-only boxes unchanged — PASS

The Title box (`TITLE:`) and Abstract box (`ABSTRACT:`) clauses are byte-for-byte
unchanged. Proven two ways: the diff shows no edit to lines 71-77, and
`test_ta1_title_and_abstract_boxes_unchanged` asserts
`((TITLE:stress) AND (ABSTRACT:cortisol))`. A direct probe confirmed
`_group_to_lucene({"title":"stress","abstract":"cortisol","both":""})` →
`(TITLE:stress) AND (ABSTRACT:cortisol)`.

### 3. Discover check inherits the fix — PASS

`europepmc_term_count` (src/discover.py:234) builds its query via
`build_europepmc_query(term_filter(term, days_back))`, and `term_filter`
(discover.py:219) constructs a "both" group — so the Discover live count now
rides the same `TITLE_ABS` change with zero code edits of its own (single source
of truth, P19). `test_ta4_check_counts_titles_and_abstracts` asserts the sent
query contains `TITLE_ABS:"kin selection"` and does not also send the bare
phrase. The user-facing wording was updated to match in routes_discover.py:164
and app.js (`liveCount`, `renderDiscoverChips`), with the corresponding test
strings advanced.

### 4. Updated test expectations are justified, not weakened — PASS

Every edited assertion moved from the *defective* expectation to the *correct*
one, and the new tests pin exact strings (P29 — `==`, not `>=`):

- AND3 parametrize cases: `(cooperati* AND survival)` → `((TITLE_ABS:cooperati*
  AND TITLE_ABS:survival))`, etc. The old string asserted the bug; the new one
  asserts the spec.
- `test_ta1_both_terms_use_title_abs` adds five cases (single word, phrase,
  wildcard, AND parts, comma alternatives) — coverage gained, none removed.
- `test_ta3_both_terms_never_bare` is a real adversarial test: it strips
  `FIELD:` tokens from the built query and asserts no bare `[A-Za-z*"]+` word
  survives except AND/OR. Probe-verified non-vacuous — it returns `[]` on a
  clean TITLE_ABS query and `['cooperati*', 'survival']` on a deliberately bare
  one, so it would actually catch a regression.
- The drift baseline advance `408deae → b384965` (tests/web/test_no_retrieval_drift.py:128)
  is a documented, deliberate W1.a exception, consistent with the dozens of
  prior advances in the same comment block. The guard still holds at HEAD
  (empty protected diff) and `test_the_guard_would_notice_a_change` still passes,
  so the advance did not blind the guard (P27).

## NOT COVERED

- TA6 live-on-production (needs a signed-in browser; recorded in the plan and
  spec_coverage).
- TA3 live completeness for the hyphen case — the plan itself marks TA3
  "partial" (see L1).
- Whether `TITLE_ABS` behaves identically to the local filter on every
  tokenisation edge is not exhaustively tested against a live index; the
  token-vs-substring gap is pre-existing and documented (discover.py:161-164).

## FINDINGS

### L1 · P7/P19 · LOW (pre-existing, surfaced by this change) · src/filtering.py:203

The local "both" match is a substring test over `title + " " + abstract`, so a
phrase with a hyphen in the paper — "kin-selection" — does not match the search
term "kin selection", while Europe PMC's `TITLE_ABS:"kin selection"` does. The
live check (first 200 records) found one such paper: 4 of 5 kept (80%). This is
the plan's "TA3 partial" and is out of TA scope (TA fixes the *query*; this is
the *filter*). Logged in TODO.md with a concrete direction ("treating hyphens as
spaces in phrase matching"). No action required for this gate.

### L2 · test clarity · LOW (non-blocking) · tests/web/test_discover_routes.py (test_ta4)

`assert ' "kin selection"' not in sent[0].replace('TITLE_ABS:"kin selection"', "")`
proves "no bare phrase" indirectly by deleting the qualified occurrence and
asserting the bare form is absent. It is correct but reads as a roundabout way
to say what `test_ta3_both_terms_never_bare` already proves generally. The
substantive assertion (`'TITLE_ABS:"kin selection"' in sent[0]`) is the load-bearing
one. Optional: replace with a direct assertion that the query equals
`build_europepmc_query(term_filter("kin selection", 90))` (already the DT9.A
contract), or drop the second line.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| TA1 | every "both" term sent as `TITLE_ABS:` (word/phrase/wildcard/AND/comma/several groups); no bare term | 5 + 3 new tests, green |
| TA1 | Title / Abstract boxes unchanged | `test_ta1_title_and_abstract_boxes_unchanged`, green |
| TA3 | no bare search word (adversarial) | `test_ta3_both_terms_never_bare`, probe-verified non-vacuous |
| TA4 | Discover check counts titles/abstracts; wording updated | `test_ta4_*` + `test_dt9c/dt9e`, green |
| TA5 | drift baseline advanced and guard still sound | 4 drift tests pass, empty protected diff at HEAD |
| TA6 | live on production | not done (needs signed-in browser) |
| full suite | `venv/bin/python -m pytest tests/ -q` | 1730 passed, 2 skipped |

---

## Notes

- One cold review pass ran. It independently confirmed the single-source routing
  (PubMed shares `build_europepmc_query`; Discover shares `term_filter`), the
  unchanged Title/Abstract clauses, and that the test edits moved toward the
  spec rather than away from it.
- No fix commit was introduced. The loop's stopping condition — no finding of
  MEDIUM or higher requiring a fix — is met; the two LOW findings go to the
  backlog (L1 already has a TODO entry).
