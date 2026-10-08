# QA Gate — wildcard on a hyphenated word (2026-10-07, HW1–HW3)

**Range:** `git diff origin/main...HEAD` (`a4b72da`..`379d447`)
**Commits:** b0a74f4 (plan), 155cc75 (fix), 379d447 (drift baseline advance)
**Plan:** `docs/implementation_plan_2026-10-07_hyphen_wildcard.md` (HW1–HW3)
**Reviewer:** learning-qa — cold pass on the materialized diff (did not know how the
code was written) plus an independent verification pass by the gate runner
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1791 passed, 2 skipped** (green)

## Verdict: REJECTED

One HIGH finding blocks approval: `_term_clause` now recurses infinitely
(`RecursionError`) on any wildcard term whose stem ends in an operator word —
`OR*`, `AND*`, `NOT*`, `ANDNOT*`, and any `… <OP>*` term. This is a regression
against `origin/main` (the old code quoted the stem and dropped the `*`), it is
reachable from the web search (no input validation rejects it), and it crashes
both the Europe PMC and arXiv builders. Verified by execution, not argument.

The three named checks otherwise pass: the query is a superset of the local
filter for the hyphen cases, the injection protection in the *code* still holds,
and arXiv shares the behaviour through the same `_term_clause`.

---

## The gate checks

### 1. Query stays a superset of what the local filter keeps — PASS (Europe PMC)

Traced `_term_clause` against the local filter's matcher
(`src/filtering.py::term_matches` → `match_term` → `wildcard_pattern`, and
`src/search_terms.py::normalise_text`). The filter reads `COVID-1*` as the
normalised phrase `covid 1*` and keeps any title containing `covid 1` (word-boundary
anchored); the query sends `TITLE_ABS:COVID AND TITLE_ABS:1*`, which matches every
such title (the `1` is always a word start, since it follows the literal space in
`covid 1`). `kin-select*` → `kin AND select*` holds the same way, and the
`SARS-CoV-*` → `SARS AND CoV` case is deliberately looser (no wildcard on the last
word), which is still a superset. `test_hw2_query_is_a_superset_of_the_filter`
exercises this against five representative titles and is a real behavioural
assertion, not a source-text grep.

Caveat recorded, not blocking: the split character set is a hand-copied
`re.split(r"[^\w]+")` that diverges from the filter's `[^\w\s*]` on `*` (see F3).

### 2. Quoting / injection protection still holds — PASS in code, one gap in the test

The rewritten `test_td1_injection_stays_inside_one_clause` was re-traced end to
end. For `x) OR (TITLE_ABS:*` the new code emits
`(TITLE_ABS:x AND TITLE_ABS:"OR" AND TITLE_ABS:TITLE_ABS)`: the `)`/`(`/`:` are
split away, `OR` is quoted, and the attacker's `TITLE_ABS:*` wildcard is consumed
by the trailing-punctuation path (`trailing_cut`), leaving the harmless literal
`TITLE_ABS:TITLE_ABS`. No widening. The split produces only `\w+` words, each sent
either quoted (operators) or under the fixed field prefix, so no clause can escape
its `TITLE_ABS:` prefix. The test's `values`/`rest` assertions correctly reject an
unquoted operator and any stray non-`AND` token; it is not weaker than the old rule
except on one operator — `ANDNOT` is omitted from the `("AND", "OR", "NOT")` tuple
(see F2).

### 3. arXiv shares the behaviour — PASS

`_arxiv_clause` builds its clauses through the same `_term_clause`
(`src/sources/query_builder.py:379`). `build_arxiv_query` for `COVID-1*` produces
`((all:COVID AND all:1*)) AND submittedDate:…`, asserted by
`test_hw1_arxiv_splits_the_same_way`. arXiv's partial `*` support (documented in
TD3: `all:cooperati*` → 50 vs `all:cooperation` → 3,273) is pre-existing and
unchanged; the old `all:"COVID-1"` exact phrase was strictly narrower, so this is an
improvement, not a regression.

### 4. Full suite — PASS

`venv/bin/python -m pytest tests/ -q` → **1791 passed, 2 skipped, 4 warnings**
(deprecation warnings only). No test covers an operator-wildcard term, which is why
F1 is green in CI.

---

## Findings

### F1 · HIGH · `src/sources/query_builder.py:86` — infinite recursion on operator-wildcard terms

A term ending in `*` whose stem splits to an operator word as its last word recurses
forever. For `OR*`: stem `OR`, `_needs_quotes("OR")` is true, `words = ["OR"]`,
`trailing_cut` is false, `words[:-1]` is empty, and line 86 calls
`_term_clause("OR*")` again with the identical argument → `RecursionError`. Triggered
by `OR*`, `AND*`, `NOT*`, `ANDNOT*`, and any `…<OP>*` (`foo AND*`, `x) OR*`). The old
code had a single-word fallback (`return _quoted(stem)`) that this change removed.

- **Risk:** user-reachable crash (500) in the web search for both Europe PMC and
  arXiv. `src/filter_vocabulary.problems()` validates only facets and institution, so
  a text term like `OR*` reaches the builder unchecked.
- **Fix:** when the last word still needs quotes (is an operator) as a bare `\w` word,
  send it without the wildcard instead of re-appending `*` — e.g. replace the
  `words[-1] + "*"` branch with `_term_clause(last, field)` when `_needs_quotes(last)`,
  so `OR*` → `TITLE_ABS:"OR"` (the pre-HW result).
- **Confidence:** high (reproduced by execution against `build_europepmc_query` and
  `build_arxiv_query`).

### F2 · LOW · `tests/test_query_builder.py:516` — rewritten injection test omits `ANDNOT`

The operator tuple `("AND", "OR", "NOT")` does not include `ANDNOT`, which is in
`_OPERATORS`. The code still quotes `ANDNOT` correctly (traced); only the test would
not catch a future regression that stopped quoting it. The old rule's
`all(t in ("Q", "AND"))` would have caught `ANDNOT` appearing bare. No code change
needed now; add `ANDNOT` to the tuple (or reference `_OPERATORS`).

### F3 · LOW · `src/sources/query_builder.py:81` — split regex is a hand-copied divergence

The plan promises to split "on the same characters the local filter treats as
spaces" (`src/search_terms.py`), but the builder re-derives the set as
`re.split(r"[^\w]+")` while the filter's `_PUNCTUATION = [^\w\s*]` excludes `*`. The
two agree on every realistic input (the trailing `*` is stripped first; `_` is `\w`
on both sides), and differ only on a malformed internal `*` (`foo*bar*`), where the
query splits on `*` but the filter keeps it. No superset violation on real input.
Recorded as a drift hazard: the character set should be a single shared source
(`normalise_text` or its punctuation class), not two copies.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| HW1 | hyphenated/punctuated wildcards split into AND parts, `*` on the last; plain `adolescen*` and spaced `kin select*` unchanged | 5+1 tests, green |
| HW2 | query is a superset of the filter (P7) | traced + 2 tests, green |
| HW3 | live `COVID-1*` > 0 hits; drift baseline advanced; CI green | recorded in plan; baseline 155cc75; suite green |
| — | no crash on operator-wildcard terms (`OR*` etc.) | **FAIL** — RecursionError (F1) |

---

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P2, P7, P19, P26, P27,
P29. Matcher semantics traced through `src/filtering.py` (`term_matches`,
`match_term`, `wildcard_pattern`), `src/search_terms.py` (`normalise_text`,
`and_parts`, `_PUNCTUATION`), and `src/sources/query_builder.py`
(`_term_clause`, `_lucene_clause`, `_arxiv_clause`).

Not covered: live source semantics (arXiv's actual `all:1*` recall, pre-existing
TD3 partiality); production saved filters; logic/algorithmic correctness beyond the
matcher, concurrency, authn/authz, and performance are outside the learning-qa
family. The reviewer emulated the sweep by direct trace plus execution, not a
delegated cold pass — see Notes.

---

## Notes

- The HIGH finding is the textbook "fix commit introduces a new bug" shape (P26):
  the change removed the old single-word fallback and, for operator-wildcard terms,
  replaced it with a recursive self-call. It is exactly the kind of defect a
  re-sweep of the fix commit exists to catch.
- The drift-baseline advance (`ee3e301` → `155cc75`) is coherent: the test compares
  current query output against the recorded baseline, and the HW change is the
  intended, documented shift — not a masked regression.
