# QA Gate — Discover Terms find papers (2026-10-07)

**Range:** `git diff origin/main...HEAD`
**Commits:** 37554b8, b383efb, 2b5b2ce
**Plan:** `docs/implementation_plan_2026-10-07_discover_terms.md` (DT6–DT8)
**Reviewer:** learning-qa (failure-pattern sweep), cold pass via delegated leaf agent
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1632 passed, 2 skipped** (green)

## Verdict: REJECTED

One high finding (the crux wiring of DT6 is untested) and two medium findings.
All three were independently confirmed against source by the gate owner.

---

## Reviewer report

```
RANGE:       git diff origin/main...HEAD (caller-supplied), 3 commits
COMMITS:     3 commit(s):
  - 37554b8 docs: plan for Discover Terms that find papers (DT6-DT8)
  - b383efb feat: Discover Terms suggest terms that find papers (DT6-DT8)
  - 2b5b2ce fix: Discover hit counts use whole words and only the papers the model saw
APPLICABLE:  repo P2 (silent drop), repo P5 (sibling consistency), repo P13 (silent
             narrowing), generic P19 (producer/consumer drift + source-text corollary),
             P25 (wired-but-unreachable), P27 (cannot-fail tests), P29 (floor assertions),
             P32 (parallel predicate copies)
CHECKED:     repo P1–P14 plus generic P19, P21, P22, P25, P27, P29, P32; matcher semantics
             verified against src/filtering.py match_term/split_terms and
             src/sources/query_builder.py _lucene_term; DT6/DT8 chain traced end to end
             (route → result shape → pollDiscover → renderDiscoverChips →
             createFilterFromTerm/addTermAsGroup)
NOT COVERED: no path narrowing. Scope-limit families not assessed: logic/algorithmic
             correctness, concurrency/races, authn/authz, injection/security (incl.
             prompt-injection of the description, which sits outside <papers>), performance,
             dependency/supply-chain, API-contract compatibility, test quality (broad),
             architecture. Also not run here: the retrieval-drift baseline, and live
             model/source behaviour (DT-R4, human-only).
```

## Findings (ranked)

### 1 · P25/P19 · HIGH · `web/static/app.js:2008`
**Risk:** The crux line `state.discoverDays = Number(job.result.days_back) || null`
is never exercised by a test. DT6.B stubs `state.discoverDays: 90` directly and
bypasses `pollDiscover`, so deleting or breaking this line silently regresses every
term-filter to 7 days with the full suite still green. The producer (server result
`days_back`) and the consumer (this line) form a contract with no test that the
consumer reads what the producer writes — the exact drift that caused the original
"no results" bug (window mismatch).
**Fix:** Add a node-run test that drives `pollDiscover` with a stubbed `api()`/job
result carrying `days_back: 90` and asserts `state.discoverDays === 90` afterward
(the repo already node-runs `pollDiscover` at `test_frontend_wiring.py:2788` for the
disabled-button path), or asserts `createFilterFromTerm` yields 90 after such a poll.

### 2 · P19/P32 · MED · `src/discover.py:119-121`
**Risk:** `_whole_word_pattern`'s `*` branch is a verbatim copy of
`src/filtering.py:152-155` (`match_term`'s wildcard: `(?<!\w)` + `re.escape(prefix)`).
A third hand-maintained matcher whose docstring says "as in the filter" by copy, not
by reference, with no agreement test. A future change to filter wildcard semantics
will silently diverge the chip counts.
**Fix:** Extract a shared wildcard-pattern helper into `filtering.py` next to
`match_term` and call it from both, plus a test asserting `_whole_word_pattern(t)`
and `match_term(t, …)` agree for `*` terms.

### 3 · P19-corr/P27 · MED · `tests/web/test_frontend_wiring.py:3189, 3242-3243`
**Risk:** Two new tests assert on source text instead of behaviour:
`assert body.count("${note}") == 2` and `assert "papers_sampled === 0" in body` /
`"none had a title" in body`. Each passes while the wiring is wrong (message text in a
dead branch, `${note}` interpolated but `note` miscomputed, a third save path missing
the note) and breaks red on a benign reword.
**Fix:** Replace with the behavioural node-run pattern the sibling tests already use:
run `addTermAsGroup` with a stubbed `saveTermFilter` that captures the message and
assert the note text is present/absent; render-test `pollDiscover`'s untitled branch
with a stubbed job rather than grepping prose.

---

## Notes

- The deliberate divergence of `count_term_hits` (whole-word, not `match_term`'s
  substring) is disclosed as "a guide, not a promise" in both the docstring and the
  plan, and is NOT itself a finding — only the unshared copy-paste of the `*` branch
  is (finding 2).
- The zero-hit "flag not hide" (DT8.D) and found-but-untitled `papers_sampled === 0`
  handling (DT8) are genuine P2 improvements and check out.
- Test suite is green (1632 passed, 2 skipped); the reject is on coverage/contract
  grounds, not a failing test.
