# QA gate — the title-or-abstract box also matches the authors' keywords (2026-10-08, KW1–KW10)

RANGE:   origin/main...HEAD (3 commits)
COMMITS: 188c243 docs: plan KW1-KW10 - match author keywords in the title-or-abstract box
         ec56a18 feat: the title-or-abstract box also matches authors' keywords (KW1-KW10)
         9d160b3 test: advance the retrieval-drift baseline to the KW commit

Plan: docs/implementation_plan_2026-10-08_author_keywords.md
Protected path touched: src/sources/schema.py (KW1), src/sources/europepmc.py and
osf.py (KW2), src/sources/dedup.py (KW3), src/sources/query_builder.py (KW4) — all
flagged W1.a in the plan. Drift baseline advanced once, ec56a18, with the reason
named in test_no_retrieval_drift.py; the guard test_the_baseline_commit_is_reachable
is intact.

Reviewer: learning-qa — one cold review pass (delegated, given only the diff and
range) plus an independent verification pass by the gate runner.

## Verdict: APPROVED

All five gate checks pass and the full suite is green. The cold pass returned
2 findings, 0 HIGH; both are non-blocking (one MEDIUM-confidence test-model note,
one LOW-confidence cap-interaction note). No fix commit was introduced — the
loop's stopping condition (no finding of MEDIUM-or-higher requiring a code change)
is met. Both findings go to the backlog.

---

## The five gate checks

### 1. The Europe PMC query stays a superset of the local filter (P7), injection-safe — PASS

- Every `both`-box part is sent as `(TITLE_ABS:x OR KW:x)`; the `OR` only widens,
  so no paper the local filter keeps is narrowed away by the query. The `KW:` arm
  is built by the *same* `_term_clause` path as `TITLE_ABS:` (quoting/escaping of
  syntax and operator words, wildcard handling), so it adds no new injection
  surface. `_title_abs_or_keyword` (query_builder.py:104) delegates to
  `_lucene_term`, which is `_term_clause`.
- KW7 (`test_kw7_query_is_a_superset_of_the_filter`, 6 cases) proves filter ⊆ query
  for the KW4 cases (phrases, wildcards, AND parts, split-hyphen wildcards,
  comma alternatives) with an in-test matcher, exactly as HW2 did.
- Injection test `test_td1_injection_stays_inside_one_clause` was updated, not
  weakened: it now also pins `rest.count(" OR ") == 1` (the one structural OR that
  joins each part's TITLE_ABS and KW forms), and still asserts no value is an
  operator word. The injected `x) OR (TITLE_ABS:*` is neutralised to quoted/word
  clauses.
- Title and Abstract boxes are unchanged (`field == "TITLE_ABS"` is the only
  branch that wraps); arXiv and OSF queries are unchanged (KW4 test asserts no
  `KW:` appears for a title/abstract-only group, and `osf_title_terms` is untouched).

### 2. Each keyword is a separate field; a phrase cannot span two keywords — PASS

`keyword_fields` (filtering.py:193) returns the keywords as a list, and both
`text_group_matches` and `within_matches` pass them as `*keywords` into
`term_matches`, so each keyword is its own field — identical to how TD5 keeps title
and abstract apart. `term_matches` requires each AND part to match *within one
field*, so a phrase cannot join the end of one keyword to the start of the next.
Pinned by `test_kw6_phrase_does_not_span_two_keywords`
(`["internal family", "systems theory"]` does NOT match `"family systems"`), and by
`test_kw6_title_box_ignores_keywords_and_no_keywords_is_unchanged` (the Title box
does not match a keyword-only paper; a paper with no/empty/non-string keywords
behaves as before).

### 3. Keywords uncapped (P9) and merged across sources — PASS

Both `[:10]` caps removed: europepmc.py:356 and osf.py:256 now keep every keyword
(filtering to non-empty strings only). `test_kw2_all_keywords_kept` builds a
25-keyword record (past the old cap of 10) and asserts all 25 survive and a match
on the 25th is kept by the filter, for both adapters. De-duplication merges via
`merged_keywords` (dedup.py:105): union in first-seen order with case-insensitive
duplicates dropped; `test_kw3_keywords_merged` covers both source orders and shows a
keyword-less bioRxiv record ends up with Europe PMC's keywords. `to_dict`/`from_dict`
carry `keywords` (schema.py:89, 153; KW1 round-trip test).

### 4. Old pinned query tests updated correctly, not weakened — PASS

Every pinned `TITLE_ABS:`-only expectation now carries its `OR KW:` form (and3, ta1,
ta3, td1, hw1, hw-gate-f1), each new string checked by eye per the plan. The one
substantive loosening — `set(rest) <= {"AND", "OR"}` where it was `{"AND"}` — is
paired with a new, stricter `rest.count(" OR ") == 1`, so it is a tightening, not a
weakening (the new clause legitimately introduces exactly one OR per part). The
drift baseline advanced 2e1f1e0 → ec56a18 with a comment naming KW1–KW4 as the
reason, and the reachability guard is intact.

### 5. textContent only for the new keywords line — PASS

The detail view sets `$("modal-keywords").textContent = keywords` and toggles a
`hidden` class; `keywordsText` (app.js) is a pure string-returning helper. No
`innerHTML` anywhere in the new code — the only `innerHTML` occurrences in app.js
remain the two pre-existing comment lines that *warn against* it.
`test_kw8_keywords_text` runs the real helper in node and asserts exact output
including the empty/`null`/non-string cases; `test_kw8_modal_and_labels` asserts the
`textContent` assignment and the class toggle, and that the page copy no longer says
"title or abstract".

## Full suite

venv/bin/python -m pytest tests/ -q  →  1888 passed, 2 skipped, 4 warnings

The 2 skips are pre-existing environment/CI skips (RLIMIT_AS Linux-only; Python
version check), unrelated to this change. All KW1–KW10, TD1, TA1/TA3, HW1/HW2, and
KW7/KW8/KW9 tests run and pass.

## Findings

F1 · P19 (test model vs real producer) · medium · confidence med — the KW7 superset
    proof rests on `_matches_query` (tests/test_query_builder.py:694), whose bare-word
    branch matches by substring (`value.lower() in w`), looser than real Lucene
    `KW:term` token matching. In principle a keyword-only paper the local filter keeps
    via substring (e.g. term "leader" vs keyword "self-leadership") could be missed by
    the real query while the test stays green. Verified non-blocking: the parametrised
    KW7 terms are all phrases/wildcards/AND parts, so the bare-word branch never
    fires on a term that could hide a miss; and the substring-vs-token gap is the
    pre-existing TITLE_ABS semantics (the app already matches bare terms by substring
    locally and by token at the source), not introduced by KW. Fix (backlog): make the
    model's bare-word branch token-equivalent, or add one canned real Europe PMC
    response asserting a keyword-only paper survives; at minimum name the approximation
    in the docstring.

F2 · P13 (keyword-inflated truncation) · low · confidence low — uncapped/merged
    keywords grow candidate counts into each source's Max-results cap. No silent drop
    today: the "limit summary" already reports truncation (plan §4). The interaction is
    untested; a live KW10-style run is the confirmation. No code change unless that run
    shows the summary failing to fire.

Notes (below-medium, backlog):
- `merged_keywords` (dedup.py) dedups on the stripped/lowered key but appends the
  unstripped `k`; the adapters keep `k` (not `k.strip()`) for non-empty keywords.
  Cosmetic only — matching normalises via `normalise_text` — but a keyword with
  surrounding whitespace would be stored un-stripped.
- europepmc.py `keywordList.keyword` and osf.py `tags` are read as already-list-shaped;
  a single-string value would be iterated character-by-character. Pre-existing (the old
  `list(keywords)[:10]` had the same shape); E8 defensive-read gap, not a KW regression.

## Scope

Failure-pattern families assessed: repo P1–P14 plus generic P2, P3, P4, P5, P7, P9,
P10, P13, P14, P19, P21, P22, P26, P35, and external-api E1–E8. Matcher semantics
traced through src/filtering.py (keyword_fields, text_group_matches, within_matches,
term_matches, match_term, wildcard_pattern), src/search_terms.py (and_parts,
match_words, normalise_text), src/sources/query_builder.py, schema.py, europepmc.py,
osf.py, dedup.py, and the KW test files.

Not covered: live Europe PMC field semantics beyond the plan's KW10 live check
(30 papers, the 3 keyword-only PubMed papers 34950063 / 35002818 / 42325314 kept);
production saved filters cannot be read from this machine; logic/algorithmic
correctness, concurrency, authn/authz, performance, and dependency risk are outside
the learning-qa family.
