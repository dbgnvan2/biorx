# QA gate — browser-run fixes (F1–F4)

RANGE: origin/main..HEAD
COMMITS: 1 — `ce52025 fix: four defects found by the browser run, with its report and screenshots`

VERDICT: REJECTED

The four fixes are individually sound (F1/F2/F3/F4 all traced and their tests
pass), but the full suite is RED: the W1.a retrieval-drift guard fails because
the commit changed retrieval-layer files without advancing the guard's baseline
or flagging the exception. Per the gate rule "full suite green", a red run is a
reject regardless of how good the fixes are.

## Test run

```
venv/bin/python -m pytest tests/ -q
1 failed, 1584 passed, 2 skipped  (73s)
```

The single failure:

```
tests/web/test_no_retrieval_drift.py::test_retrieval_modules_unchanged_since_the_web_work_began
E  the web app modified retrieval code it was supposed to wrap:
   ['src/sources/biorxiv_medrxiv.py', 'src/sources/crossref.py',
    'src/sources/europepmc.py', 'src/sources/orchestrator.py']
```

## FINDINGS (ranked)

### F1 (blocker) — W1.a retrieval-drift guard left red (process/contract)

- file: `tests/web/test_no_retrieval_drift.py:135` (baseline at line 92, still `dd0f496`)
- The commit legitimately changes four protected retrieval files (F2 touches
  `biorxiv_medrxiv.py` + `orchestrator.py`; F4 touches `crossref.py` +
  `europepmc.py`). The guard's own contract — "If the change is genuinely
  required, say so in the commit and update this test deliberately" — was not
  met: the `BASELINE` constant was not advanced to `ce52025` and the commit
  message carries no W1.a flag.
- The changes ARE genuinely required (real bugs), so this is not "don't touch
  retrieval code". It is the repository's established deliberation ritual being
  skipped, which is exactly what the guard exists to prevent silently passing.
- risk: high — a red suite blocks CI and any other gate; also the invariant's
  audit trail is broken.
- fix: advance `BASELINE` to `ce52025` with a one-line comment naming F2/F4 as
  W1.a-required, and note it in the commit message. Re-run the suite to green.

### F2 (low) — single-letter inline tags eat unescaped "a<b and c>d"

- file: `src/sources/markup.py:21` (the `_INLINE` list contains `b|i|u|a`, and
  every regex is `re.IGNORECASE`)
- `markup_to_text("a<b and c>d")` returns `"ad"`, not `"a<b and c>d"`: `<b and c>`
  is read as a `<b>` tag with attributes. Same for `<i>`, `<u>`, `<a>` and their
  uppercase forms.
- This is a strict improvement over the old Crossref helper (which ate
  `"p < 0.05 … x > 1"` → `"p  1"`), and the realistic case — spaces around the
  comparison signs — is handled and tested
  (`test_br2_less_than_and_greater_than_in_text_survive`). But for Europe PMC
  this stripping is a NEW capability (before, tags were simply shown), so this is
  a new, narrow regression window on unescaped input. `p<0.05`, `n<5`, `x<10`
  are safe (the char after `<` is a digit, not a tag letter); only a letter in
  `b/i/u/a` immediately after `<` with a later `>` triggers it.
- risk: low — malformed/unescaped prose with a no-space single-letter inequality
  is uncommon in biomedical abstracts, and Europe PMC's `<p>`/`<h4>` content is
  the normal path.
- fix (optional, not blocking): drop the bare single-letter names `b|i|u|a`
  from `_INLINE`, or require a word boundary / closing tag lookahead, so a
  standalone letter after `<` is left as text.

## Checks requested, and their outcome

- F2 orchestrator change vs every adapter — CORRECT. `has_more` is read via
  `getattr(adapter, "has_more", None)`; only `BiorxivMedrxivAdapter` defines it
  (grep confirms). For the five other adapters, and for the `_PagedAdapter` /
  `MagicMock` test doubles, `getattr` yields `None` (not a bool), so
  `last_page_full = page_size_seen >= self.PAGE_SIZE` — the old rule,
  byte-for-byte the prior behaviour. The two `page_size_seen < PAGE_SIZE` break
  sites were changed to `not last_page_full`, which is equivalent under the old
  rule. Adapter cursor logic traced against the 95/40 test pools: biorxiv
  cursors [0,30,60,90], medrxiv [0,30], total 135 — correct. The one-page
  over-read past `max_results` (250 → 300 read) is inherent to page granularity
  and is reported as truncated, not silent.
- F4 vs scientific text containing `<` and `>` — CORRECT for the common case,
  one residual edge (F2 above). `"p < 0.05 … x > 1"`, `"p<0.001"`, `"age<60"`,
  `"n<5"`, `"x <= y"`, `"A < B < C"`, `"5:1"`, `"Fig. 2:"` all pass through
  unchanged; `<sub>`/`<i>`/`<p>`/`<h4>` and `&lt;/&gt;` entities are handled
  correctly.

## Positive verification (no defects found)

- F1 (Ollama think flag): `llm_config.yaml` → `provider_config().thinking` →
  `build_client()` → `OllamaClient.thinking` → `payload["think"]`; empty setting
  sends nothing. Wired and tested (`test_ollama_config_disables_thinking_for_qwen35`,
  `test_ollama_think_flag_follows_config`).
- F3 (keyless providers): `keyless_providers` derives from
  `provider_config(...).needs_key` = `bool(api_key_env)`, read from config not
  hard-coded; all three providers use `api_key_env`, so only `ollama` is
  keyless. The page refuses a key before saving and clears a stale browser key
  on switch; `renderMe` now resets the placeholder. Tests pass.
- F4 shared helper: Crossref's `_strip_jats` now calls the one `markup_to_text`,
  so the two sources share a single implementation rather than drifting
  (P19 concern addressed). The canonical id still uses the raw title, so stored
  paper ids do not change.

## Not covered

- Live API re-check of bioRxiv/medRxiv paging against the real endpoint (the
  report's numbers — 30/call, 3,433+1,491 papers — were taken from direct calls
  at run time and are quoted, not re-verified here).
- The five "found, not fixed" items in the browser-run report are recorded in
  TODO.md and are out of scope for this gate.
