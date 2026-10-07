# Implementation plan — Discover offers only terms that find papers (2026-10-07, DT9)

**Request:** "this search returns nothing.... cooperative species survival" →
"Discover terms should return workable search phrases!"
**Status:** PLAN — awaiting owner approval. No code written.
**Follows:** `docs/implementation_plan_2026-10-07_discover_terms.md` (DT6–DT8, deployed `3667a15`).
**Spec extended:** `docs/web_parity_spec_2026-09-17.md#FP1-B`.

---

## 0. Why DT6–DT8 were not enough

DT8 counts each term against the ~30 papers the model was shown, and **flags**
a term with no hits. That decision (plan §3, "flag, not hide") was wrong for
the owner's purpose: Discover still offered a term that finds nothing, and the
count it showed was measured on the sample, not on the search the term goes
into. The model can still produce a phrase like "cooperative species survival"
despite the prompt.

Live Europe PMC counts, 2026-10-07:

| Query | 14 days | 90 days |
|---|---|---|
| `"cooperative species survival"` (what the app sends) | 0 | 0 |
| cooperative AND species AND survival | 79 | 1,147 |
| cooperati* AND survival | 460 | 6,380 |

**Rule from now on:** a term is offered only if a search for it, as the app
will send it, returns papers. A term that cannot be checked is offered and
labelled "not checked" (transient ≠ terminal, P1). A term that was dropped is
named, not hidden silently (P2).

---

## 1. Design

1. **Live check (DT9.A).** After the model answers, each term is checked with
   one Europe PMC count request: the query is
   `build_europepmc_query({"text_groups": [{"both": term}], "days_back": D})`,
   i.e. exactly what a filter made from the term sends to Europe PMC, over the
   same window (D = `discover.days_back`). Europe PMC is the main source and
   indexes bioRxiv, medRxiv and PubMed; the other sources are not checked, and
   the page says "in Europe PMC".
   - New function `europepmc_term_count(term, days_back, session, timeout)` in
     `src/discover.py`. It uses `with_retry` (base.py) and `BASE_URL`, and
     returns `None` on failure — **not** 0. The adapter's own `get_total`
     returns 0 on failure, which would drop good terms during an outage, so it
     is not used. No protected file changes (`query_builder.py` is called, not edited).
   - Timeout, retry and the per-term request gap come from config
     (`discover.check_timeout_s`, `discover.check_delay_s`), respecting the
     Europe PMC rate limit. Up to 10 terms → up to 10 requests, ~2–5 s total.
2. **One replacement round (DT9.B).** If any term counts 0, the model is asked
   once more: "These terms find no papers: … Replace each with a term of 1–3
   words that appears word for word in the papers." The replacements are
   checked the same way. One extra model call at most; both calls' tokens are
   added (`TokenUsage.__add__`) and billed as one job through the existing
   `BilledCall`. The replacement prompt is config (`discover.replace_prompt`).
3. **Drop and name (DT9.C).** Terms still at 0 after the round are removed from
   `terms` and returned in `dropped: [{term, reason: "0 papers in Europe PMC, last 90 days"}]`.
   The page shows one line: "Not offered (found no papers): …".
4. **Not checked (DT9.D).** A term whose count request failed stays in `terms`
   with `live_hits[term] = null`; its chip says "not checked" and the page says
   "Europe PMC did not answer; N terms were not checked." No replacement round
   is spent on unchecked terms.
5. **Chip shows the live count (DT9.E).** "1,147 in Europe PMC" replaces
   "n of 30" as the visible badge; the sample count moves to the tooltip.
   Zero-count chips no longer occur (they are dropped), so the DT8.D dashed
   style is used only for "not checked".
6. **Phase text (DT9.F).** The job phase shows "Checking terms in Europe PMC (3 of 8)…".

Result shape additions (back-compatible): `live_hits: {term: int|null}`,
`dropped: [...]`, `replaced: {old: new}`. `terms`, `term_hits`,
`papers_sampled`, `days_back` unchanged.

---

## 2. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| DT9.A | Each term is counted with the exact Europe PMC query a filter made from it would send, over `discover.days_back`. | `tests/web/test_discover_routes.py::test_dt9a_check_uses_the_filters_own_query` — fake session records params; asserts the query equals `build_europepmc_query` for `{"both": term}` with the configured window |
| DT9.A2 | A failed count is `None`, never 0. | `test_dt9a2_failed_count_is_none_not_zero` (timeout, 5xx after retries, 429, non-JSON body) |
| DT9.B | Terms with 0 hits trigger exactly one replacement request; replacements are checked; tokens of both calls are summed and recorded once. | `test_dt9b_zero_terms_are_replaced_once`, `test_dt9b_two_model_calls_are_billed_as_one` |
| DT9.C | Adversarial (P7): "cooperative species survival" with a live count of 0 is never in `terms`; it is in `dropped` with its reason; a 1,147-hit term next to it is kept. | `test_dt9c_zero_hit_phrase_is_not_offered` |
| DT9.C2 | If every term is dropped, the result says so ("No suggested term found papers") rather than an empty chip area that reads as "no ideas". | `test_dt9c2_all_dropped_is_reported` + node-run page test |
| DT9.D | Dirty state (P8)/transient: Europe PMC failing for some terms keeps those terms, `live_hits` null, no replacement round for them. | `test_dt9d_unchecked_terms_are_kept_and_labelled` |
| DT9.E | The chip shows "N in Europe PMC" or "not checked"; dropped terms are listed on one line; old results (no `live_hits`) render as today. | `tests/web/test_frontend_wiring.py::test_dt9e_chip_shows_live_count`, `test_dt9e_dropped_terms_are_listed`, `test_dt9e_old_result_unchanged` (node-run `renderDiscoverChips`, not source greps) |
| DT9.F | Phase text during the check. | `test_dt9f_phase_names_the_check` |
| DT9.G | Settings in config: `check_timeout_s`, `check_delay_s`, `replace_prompt`; missing keys warn and use defaults. | `test_dt9g_check_settings_come_from_config`; `test_dt5_repo_config_has_a_discover_block` extended |
| DT9-R | Existing DT1–DT8 tests pass; DT8.D test updated **only** where its premise changes (a zero-count term is now dropped, not flagged) — called out in the commit. Retrieval-drift baseline not advanced. CI green. | full suite; `test_no_retrieval_drift.py` |
| DT9-L | Live check on production: run Discover with "cooperative species survival"; every offered term, clicked into a filter and run, returns ≥ 1 paper. Record terms and counts. | **Human/browser check** (live LLM + live source); needs the in-app browser already signed in — Claude cannot enter the access code or PIN |

**Untested by design:** the live Europe PMC request itself (mocked session in
tests); flagged here, covered only by DT9-L.

---

## 3. Build order

1. DT9.A2, DT9.A tests → `europepmc_term_count`.
2. DT9.C, DT9.D tests → check-and-drop in `_run_discover` (no replacement yet).
3. DT9.B tests → replacement round, token summing.
4. DT9.G config; DT9.F phase.
5. DT9.E page; DT8.D test premise update.
6. Docs (spec FP1-B, coverage, CHANGELOG), full suite, `/chdp` (Hermes gate), push, CI, DT9-L.

---

## 4. Owner decisions (defaults chosen)

- **Drop instead of flag** — reverses DT8 §3 per the owner's request; dropped
  terms are still named on one line.
- **Europe PMC only** for the check. Checking all nine sources per term would
  take ~1 minute and hit bioRxiv's Railway 429s.
- **One replacement round**, not a loop, to cap model cost at two calls.

## 5. Still open, not in this plan

- The Search tab box and the filter's Title/Abstract/Both fields still treat a
  multi-word entry as one exact phrase, and there is no "all of these words"
  option. That needs a `query_builder.py` change (protected) and its own plan.
