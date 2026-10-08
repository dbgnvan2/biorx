# QA Gate (re-gate) — wildcard on a hyphenated word (2026-10-07, HW1–HW3)

**Range:** `git diff origin/main...HEAD` (`a4b72da`..`b34f53d`)
**Commits:** b0a74f4 (plan), 155cc75 (fix), 379d447 (drift baseline advance),
5eafea4 (fix F1–F3), b34f53d (drift baseline advance)
**Plan:** `docs/implementation_plan_2026-10-07_hyphen_wildcard.md` (HW1–HW3)
**Prior gate:** `docs/cycles/2026-10-07_hyphen-wildcard-qa-gate.md` — REJECTED
(F1 HIGH infinite recursion, F2 LOW, F3 LOW). This re-gate reviews the fix
commit `5eafea4` plus the whole range, and re-verifies all three findings.
**Reviewer:** learning-qa — cold delegated pass over the materialized diff at
`/tmp/sweep.diff` (did not know how the code was written), plus an independent
verification pass by the gate runner.
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1796 passed, 2 skipped** (green)

## Verdict: APPROVED

The three findings from the rejected gate are all FIXED and verified by
execution, not argument. The cold re-sweep of the full range returns no HIGH or
MED finding; one LOW finding (P32) is recorded below as non-blocking and
pre-existing (it matches `origin/main` behaviour and is strictly better than
the crash it replaced).

---

## Prior findings — re-verification

### F1 (HIGH) — infinite recursion on operator-wildcard terms — FIXED

`_word_clause` (`src/sources/query_builder.py:90`) is non-recursive: a word
that needs quoting (operator or syntax) is quoted and loses the wildcard, so
the `words[-1] + "*"` self-call that recursed forever is gone. Verified by
execution against both builders — every one of these terminates and returns
the expected quoted clause (Europe PMC text part; arXiv produces the same
`all:`-prefixed clause plus the date range):

| Input | Europe PMC | arXiv |
|---|---|---|
| `OR*` | `(TITLE_ABS:"OR")` | `(all:"OR") AND submittedDate:…` |
| `AND*` | `(TITLE_ABS:"AND")` | `(all:"AND") AND submittedDate:…` |
| `NOT*` | `(TITLE_ABS:"NOT")` | `(all:"NOT") AND submittedDate:…` |
| `ANDNOT*` | `(TITLE_ABS:"ANDNOT")` | `(all:"ANDNOT") AND submittedDate:…` |
| `cats OR*` | `((TITLE_ABS:cats AND TITLE_ABS:"OR"))` | `((all:cats AND all:"OR")) AND …` |
| `x-NOT*` | `((TITLE_ABS:x AND TITLE_ABS:"NOT"))` | `((all:x AND all:"NOT")) AND …` |

Regression tests added: `tests/test_query_builder.py::test_hw_gate_f1_operator_wildcard_terminates`
(five parametrized cases) asserts both the Europe PMC output and that
`build_arxiv_query` returns. These fail against `155cc75` (RecursionError) and
pass now.

### F2 (LOW) — injection test omitted `ANDNOT` — FIXED

`tests/test_query_builder.py:516` operator tuple is now
`("AND", "OR", "NOT", "ANDNOT")`, so the injection test rejects a bare
`ANDNOT` as it does the other operators.

### F3 (LOW) — split regex was a hand-copied divergence — FIXED

`import re` removed from `src/sources/query_builder.py`; `_term_clause` now
splits with `src.search_terms.match_words`, which uses the filter's own
`_PUNCTUATION = re.compile(r"[^\w\s*]")` — a single shared source of truth, not
a parallel `re.split(r"[^\w]+")` copy. `match_words` is defined in
`src/search_terms.py:37` and the builder imports it at
`src/sources/query_builder.py:23`.

---

## Edge-input sweep (gate runner)

Additional pathological inputs were executed against both builders to confirm
no new termination or escape bug (Europe PMC text part shown; arXiv mirrors it):

| Input | Europe PMC | Result |
|---|---|---|
| `*` | `(TITLE_ABS:*)` | terminates; matches everything in field (unchanged from origin/main) |
| `-*` | `(TITLE_ABS:"-")` | terminates; punctuation-only stem quoted |
| `a**` | `(TITLE_ABS:"a*")` | terminates; internal `*` kept by shared splitter and quoted |
| `"*` | `(TITLE_ABS:"\"")` | terminates; lone quote quoted |
| `foo AND*` | `((TITLE_ABS:foo AND TITLE_ABS:"AND"))` | terminates |
| `x) OR*` | `((TITLE_ABS:x AND TITLE_ABS:"OR"))` | terminates; `)`/`(` split away, no escape |

No input crashes either builder, and no clause escapes its field prefix:
`match_words` yields only runs of `[\w*]`, so an unquoted word is pure `\w+`
(no paren/colon/quote/space survives the split); any `*` forces quoting via
`_needs_quotes`; operators are quoted. The injection test's `values`/`rest`
assertions hold.

---

## Re-sweep result (cold pass)

The delegated reviewer checked repo P3, P13 and generic P2, P26, P27, P32
against the full `_term_clause`/`_word_clause`/`match_words`/filtering trace
plus live execution. Verdict: **1 finding, 0 high** — F1/F2/F3 all FIXED, no
new HIGH or MED defect from the fix commit.

### P32 · LOW · `tests/test_query_builder.py:575` — the F1 test pins the narrowing without noting it

`test_hw_gate_f1_operator_wildcard_terminates` asserts the *narrowed* output
(`OR*` → `TITLE_ABS:"OR"`) as the expected value. This is narrower than the
local filter, which reads `OR*` as the prefix pattern `(?<!\w)or` and keeps any
title with a word starting "or" (`orange`, `order`), so those kept papers are
silently missed — a superset-invariant caveat the test does not state.

- **Not a regression:** `origin/main` produced the identical narrowing for both
  the single-word case (`OR*` → `_quoted("OR")`) and the spaced case
  (`cats OR*` → `(cats AND "OR")`), traced through the old `_term_clause`
  fallback. The fix restores that behaviour in place of the `RecursionError`,
  so it is strictly better than the crash and pre-existing.
- **Trigger is rare:** `_OPERATORS` is case-sensitive, so lowercase `or*`/`not*`
  keep their wildcard correctly; only uppercase `NOT*` (and the other three) hit
  the narrowing.
- **Future note (non-blocking):** the test docstring should say operator
  wildcards are deliberately narrowed (a crash is worse; Europe PMC has no safe
  way to wildcard an operator token), so it does not read as if the narrowing is
  the intended search semantics. No code change required now.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| HW1 | hyphenated/punctuated wildcards split into AND parts, `*` on the last; plain `adolescen*` and spaced `kin select*` unchanged | tests green |
| HW2 | query is a superset of the filter (P7) | `test_hw2_query_is_a_superset_of_the_filter` green |
| HW3 | drift baseline advanced; full suite green | BASELINE → `5eafea4`; 1796 passed, 2 skipped |
| — | no crash on operator-wildcard terms (`OR*` etc.) | **PASS** (was FAIL in prior gate) |
| — | F2 (`ANDNOT` in test) | **PASS** |
| — | F3 (shared splitter, no copied regex) | **PASS** |

---

## Scope

Failure-pattern families assessed: repo P3, P13 and generic P2, P26, P27, P32.
Matcher semantics traced through `src/filtering.py` (`term_matches`,
`match_term`, `wildcard_pattern`), `src/search_terms.py` (`normalise_text`,
`match_words`, `_PUNCTUATION`), and `src/sources/query_builder.py`
(`_term_clause`, `_word_clause`, `_lucene_clause`, `_arxiv_clause`).

Not covered: live source semantics (Europe PMC / arXiv actual recall of split
words like `1*`; arXiv's partial `*` support is pre-existing TD3); production
saved filters; injection/security classes beyond field-prefix escape;
concurrency, authn/authz, and performance are outside the learning-qa family.

---

## Notes

- The fix is the P26 textbook shape done right: it did not introduce a new
  recursion, a new splitter copy, or a drift in the injection test — the three
  failure modes the prior fix commits in this repo have hit. `_word_clause`
  short-circuits quoting on the *word* level, which is exactly the non-recursive
  base case the original change was missing.
- The drift-baseline advance `155cc75` → `5eafea4` is coherent: `5eafea4` is the
  last commit to intentionally touch `query_builder.py` (a protected W1.a file),
  and the fix changed operator-wildcard output, so the baseline must move past
  it; the guard itself is verified reachable and would still notice a change.
