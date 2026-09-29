# QA gate — browser-run fixes (re-gate)

RANGE: origin/main..HEAD
COMMITS: 3 —
  `ce52025 fix: four defects found by the browser run, with its report and screenshots`
  `26327ec fix(markup): a letter after '<' is not a tag unless it is exactly <b>, <i>, <u> or <a href>`
  `27cfbac test(drift): advance the W1.a baseline for the browser-run fixes (QA gate finding 1)`

VERDICT: APPROVED

Both findings from the first gate (docs/cycles/2026-09-29_browser-run-qa-gate.md)
are answered, the W1.a guard is green, and the full suite passes.

## Test run

```
venv/bin/python -m pytest tests/ -q
1586 passed, 2 skipped, 4 warnings  (68s)
```

Previously: `1 failed, 1584 passed, 2 skipped`. The delta is exactly the two
resolutions: the previously-red drift test now passes, and the new markup test
is added (+1 each).

## Re-confirmation of the two findings

### Finding 1 (was blocker) — W1.a retrieval-drift guard

- Resolved by `27cfbac`: `BASELINE` advanced `dd0f496 → 26327ec`, with a comment
  naming F2 (bioRxiv/medRxiv paging) and F4 (source text through the shared
  `markup.py`) as W1.a-required, and `src/sources/markup.py` added to `PROTECTED`.
- `git diff --name-only 26327ec..HEAD -- src/sources/` is empty, so the guard's
  protected-set diff is clean. `BASELINE` is reachable (`git log -1 26327ec` =
  the markup fix subject). The drift guard's own four tests pass.
- The baseline advance covers every protected file `ce52025` touched
  (biorxiv_medrxiv.py, crossref.py, europepmc.py, orchestrator.py) plus
  `26327ec`'s markup.py change. The comment's F2/F4 reasons are accurate.

### Finding 2 (was low) — single-letter inline tags eat "a<b and c>d"

- Resolved by `26327ec`: `b|i|u|a` removed from `_INLINE` and moved to a new
  `_SINGLE_LETTER` pattern that matches only the exact forms
  `</?[biu]>`, `<a href=…>`, `</a>` — so a letter after `<` with anything but a
  closing `>` (or `href`) is left as text.
- `test_br2_a_letter_after_less_than_is_not_a_tag` asserts
  `"a<b and c>d"`, `"x<a and y>z"`, `"u<i and j>v"` pass through unchanged and
  that real `<i>/<b>/<a href>` tags still strip. Passes.

## Regression probe (markup differential)

Ran the edge-case battery from the first gate directly against `markup_to_text`,
all unchanged/correct (0 failures):

- prose with comparison signs: `p < 0.05 and x > 1`, `p<0.001`, `age<60`,
  `n<5`, `x <= y`, and the three single-letter prose cases — all survive.
- real tags: `<i>E. coli</i>`, `<b>bold</b>`, `<B>BOLD</B>`, `<u>under</u>`,
  `<a href="http://x">link</a>`, `<sub>reg</sub>` + entities — all stripped/decoded.

No narrowing or widening observed against the first gate's listed cases.

## Checks requested, and their outcome

- Finding 1 answered — CONFIRMED. Baseline is `26327ec`, `markup.py` protected,
  reasons recorded in the test and commit message; guard green.
- Finding 2 answered — CONFIRMED. Exact-form single-letter match; prose survives.
- Nothing else regressed — CONFIRMED. Full suite green; protected-set diff empty;
  markup edge-case battery clean.

## Not covered

- Live API re-check of bioRxiv/medRxiv paging against the real endpoint (same
  scope as the first gate — quoted, not re-verified here).
- The five "found, not fixed" items in the browser-run report remain in TODO.md
  and are out of scope for this gate.
