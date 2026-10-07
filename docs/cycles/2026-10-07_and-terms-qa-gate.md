# QA Gate — "all of these words" (AND) in one search box (2026-10-07)

**Range:** `git diff origin/main...HEAD`
**Commits:** fb48c3a (plan), 408deae (feature), e5ca638 (drift-baseline advance)
**Plan:** `docs/implementation_plan_2026-10-07_and_terms.md` (AND1–AND8)
**Reviewer:** learning-qa — one warm self-review + one cold review pass (delegated)
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1700 passed, 2 skipped** (green)

> Note: the requested baseline hash `f2e1ab8` is not a valid object in this
> repo. The drift-baseline commit is `e5ca638` ("advance the retrieval-drift
> baseline to the AND-term commit"), which is HEAD of the reviewed range.

## Verdict: APPROVED

Two findings, both **medium** (one pre-existing and explicitly deferred, one
dead code), **zero high**. The single-source-of-truth claim holds, the OSF
superset property is proven, and terms without AND are byte-for-byte unchanged.

---

## What was checked

### The single parser (single source of truth)

`src/search_terms.and_parts` is the one reader of the AND operator. Every
consumer imports it — no parallel hand-maintained copy of the split logic
exists (P19 pitfall 2):

| Consumer | Site | AND meaning |
|---|---|---|
| Local filter (the rule the user sees) | `filtering.py:term_matches` | every part must match (`match_term` per part) |
| Europe PMC / PubMed | `query_builder._lucene_clause` | `(TITLE:a AND TITLE:b)` / `(a AND b)` |
| arXiv | `query_builder._arxiv_clause` | `(ti:a AND ti:b)`, wildcard stripped per part |
| OSF / PsyArXiv / SocArXiv title | `query_builder._osf_title_part` | longest part sent as `filter[title]` |
| PsyArXiv / SocArXiv keywords | `query_builder.build_psyarxiv_query` | parts listed as separate keywords |
| Discover counts | `discover.count_term_hits` | counts only when every part present |

Verified by reading the current files and tracing every call site of
`split_terms` / `and_parts` / `term_matches` (grep across the repo). The only
two callers of `filtering.split_terms` are `text_group_matches` and
`count_term_hits`, and both lower-case per part after the case-preserving
change, so the contract change is fully contained (P22 checked).

### OSF title narrowing stays a superset of the local filter (AND5)

The sent part is literally one of the required parts (the longest, after
stripping `*`), and OSF `filter[title]` is a contains-match, which is broader
than the local filter's word-boundary prefix / substring match for that part.
So any title the local filter keeps contains the sent part, and OSF cannot
lose it. This is the safe direction: OSF may return *extra* papers (which the
local filter then drops), never fewer.

Differential probe (battery of 12 terms × 12 titles, incl. the 8-char
`cooperati*`/`survival` tie, wildcards, case, and multi-part terms):
**0 violations** — every title `term_matches` keeps contains the sent part.

### Terms without AND behave exactly as before (AND7)

Differential probe: `git show origin/main:src/sources/query_builder.py` exec'd
into an isolated namespace, compared against the working tree over a battery of
8 non-AND filters × 4 builders (`build_europepmc_query`, `build_arxiv_query`,
`build_psyarxiv_query`, `osf_title_terms`): **0 mismatches** — byte-identical.

`split_terms` old vs new (case-insensitive set comparison) differs only on the
intended cases: a term that is only `AND` (or `AND, …`) is dropped, matching
"a term that is only AND is ignored" in the plan. Lowercase `"and"` and
`"BRANDING"`/`"Rock And Roll"` are preserved as phrase parts (AND1 table).

### Discover counts agree on a term's meaning (AND6)

`count_term_hits` now applies the same comma=OR / AND=every-part structure with
its whole-word matcher. The whole-word vs substring divergence relative to the
local filter's `match_term` is the *pre-existing, documented* DT8.A design
decision (a word search is what the sources do), not introduced here. AND6 test
asserts exact counts (`{term: 2, …: 3, …: 2}`, not a floor).

### Retrieval-drift baseline (AND-R)

`tests/web/test_no_retrieval_drift.py` advanced `BASELINE` from `667e3de` to
`408deae` with a comment naming AND1–AND5 as the reason — the documented W1.a
exception, not a silent widening. The guard-reachability test (`test_the_baseline_commit_is_reachable`)
still passes, so the advance is a real commit, not a bad string (P29/P27).

---

## Findings (from the cold pass; both non-blocking)

### 1 · P19 · MED · `src/sources/query_builder.py:41-47` (`_lucene_term`)

`_lucene_term` escapes only `"`. A part that is a standalone Lucene reserved
word (`OR`, `NOT`) or contains `:` / `(` / `)` is emitted bare into the Lucene
query and parsed as syntax, while the local filter reads it literally — a
divergence in exactly the layer this feature exists to unify.

**Disposition: accepted, not fixed.** This is pre-existing (`_lucene_term` has
always escaped only `"`) and is explicitly deferred in the plan's §6 "Adjacent
issues found, not fixed" (`"A term containing (, ), : or OR goes into the Lucene
query as syntax. Not new; not in this plan."`). AND does slightly amplify it —
bare single-token parts are now the norm rather than the quoted multi-word
exception — but it requires a user to type a reserved word as a *search term*,
and it is not a regression against the prior behaviour. Recorded for the next
query-builder pass; fixing it means Lucene-escaping reserved chars in
`_lucene_term`, out of scope here.

### 2 · P21 · MED · `src/search_terms.py:29` (`is_and_term`)

`is_and_term` is defined but has zero callers anywhere in the repo
(grep-verified). Dead code that will rot and can mislead a future reader into
thinking the AND distinction is consumed somewhere it is not.

**Disposition: accepted, not fixed.** Trivial severity. The parser's real
surface is `and_parts`; `is_and_term` is a convenience predicate no consumer
needed. Delete it in a later cleanup, or use it where a branch on "is an AND
term" would add clarity.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| AND1 | `and_parts` splits uppercase ` AND ` only; trims; drops empty parts; leaves lowercase "and", "CD4+", "BRANDING" alone | 14-case table, green |
| AND2 | local filter: every part must match; adversarial "cooperation-but-not-survival" fails; commas still OR | 6 tests, green |
| AND3 | Europe PMC/PubMed exact query strings (bare, TITLE:, ABSTRACT:, phrase, wildcard, comma-mixed, multi-group, only-AND ignored) | 9 cases + 2 tests, green |
| AND4 | arXiv query: `(all:a AND all:b)`, `ti:`/`abs:`, wildcard per part | 3 cases, green |
| AND5 | OSF sends one part (longest); superset property | 3 tests + differential battery (0 violations) |
| AND6 | Discover counts need every part | green (exact counts) |
| AND7 | terms without AND unchanged | differential 0 mismatches + full suite green |
| AND8 | page hint under every text box + Search box, textContent, no markup | green |
| AND-R | drift baseline advanced with reason; guard reachable | green |

AND-L (live filter run on production) is a human/browser check and remains
pending, as stated in the plan.

---

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P19, P21, P22,
P26, P27, P29. Matcher semantics verified against `src/filtering.py`
(`match_term`, `split_terms`, `wildcard_pattern`), `src/search_terms.py`,
`src/sources/query_builder.py`, `src/discover.py`, and the drift guard.

Not covered: live source semantics — whether the emitted Lucene/arXiv/OSF
queries return the same papers the local filter keeps is integration-only
(AND3's live sanity check and AND-L are recorded in the plan, deliberately not
in the suite). Production saved filters cannot be read from this machine (also
stated in the plan). Logic/algorithmic correctness, concurrency, authn/authz,
security, performance, dependency risk are outside the learning-qa family.

---

## Notes

- One cold pass ran (delegated, did not know how the code was written); the
  warm self-review and the cold pass independently agreed the parser is
  single-source and the superset property is sound. The two findings are both
  medium and non-blocking, so no fix commit was introduced — the loop's
  stopping condition (no finding of medium-or-higher requiring a fix) is met
  without a re-sweep.
- Suite went from 1634 (prior Discover gate) to 1700 passed: this range adds
  the AND1–AND8 test coverage plus the drift-baseline advance, all green.
