# Implementation plan — Discover Terms find papers (2026-10-07)

**Request:** "I've been running some queries based on suggested terms provided by
the application and I'm not getting any results at all." → "write the plan for 1–3".
**Status:** APPROVED 2026-10-07; built. DT6–DT8 done (tests in `docs/spec_coverage_webapp.md`); DT-R4 browser check after deploy.
**Spec extended:** `docs/web_parity_spec_2026-09-17.md#FP1-B` (Discover terms).
New IDs continue the existing DT1–DT5 series in `tests/web/test_discover_routes.py`.

---

## 0. Diagnosis (verified 2026-10-07)

Two causes, both in the Discover → filter path:

1. **Window mismatch.** Discover samples the last 90 days
   (`llm_config.yaml` `discover.days_back: 90`). Clicking a term with no filter
   open calls `newFilter()`, which sets the window to 7 days
   (`web/static/app.js:1759`). The Search tab defaults to 14.
2. **Exact-phrase terms.** The prompt asks for "2–4 word" phrases
   (`web/routes_discover.py:34`). A term with a space is quoted as one phrase by
   `src/sources/query_builder.py:_lucene_term` and matched as one substring by
   `src/filtering.py:match_term`. Topic-style phrases rarely occur verbatim.

Live Europe PMC counts, 2026-10-07:

| Query | 7 days | 90 days |
|---|---|---|
| `"social isolation older adults"` | 0 | 2 |
| `"loneliness intervention outcomes"` | 0 | 0 |
| social AND isolation AND older AND adults | 86 | 2,749 |

Not verified: the owner's exact terms, and the production log (Railway). The
local `biorx.log` is empty.

**Retrieval layer untouched.** No change to any file in `PROTECTED`
(`tests/web/test_no_retrieval_drift.py`). All work is in `src/discover.py`,
`web/routes_discover.py`, `llm_config.yaml`, `web/static/app.js`,
`web/static/styles.css`. `src/filtering.py` is read (its `match_term`), not changed.

---

## 1. Acceptance criteria and tests

### DT6 — A filter made from a term searches the window the term came from

| ID | Criterion | Test |
|---|---|---|
| DT6.A | The discover job result carries the window it sampled: `result.days_back` equals `discover.days_back` from config. | `tests/web/test_discover_routes.py::test_dt6a_result_reports_the_sampled_window` (config set to 45 in the test, asserts 45 — not the default) |
| DT6.B | A filter created by clicking (or right-clicking) a term is saved with `days_back` equal to the window the terms came from, not 7. | `tests/web/test_frontend_wiring.py::test_dt6b_term_filter_uses_discover_window` — node-run `createFilterFromTerm` with stubbed `api()` and DOM, asserts the POST body `filter.days_back == 90` (fixture value), and that `newFilter()` alone still gives 7 (no change to the plain New filter button) |
| DT6.C | Adding a term to an **open** filter does not change that filter's window, but when its window is shorter than the discover window the notice says so ("This filter searches the last 7 days; these terms came from the last 90."). | `tests/web/test_frontend_wiring.py::test_dt6c_shorter_window_is_named_in_the_notice` — pure helper `windowNote(filterDays, discoverDays)` run in node: returns the note for 7/90, returns `""` for 90/90 and 120/90, and for a filter using a date range |
| DT6.D | The chip hint names the window: "Suggested from papers in the last 90 days." | `tests/web/test_frontend_wiring.py::test_dt6d_hint_names_the_window` — node-run `renderDiscoverChips` with a fake container; asserts hint text contains the number from the result, not a literal in JS |

The window comes from the job result (DT6.A), never a constant in `app.js`
(rule 8). If an older result has no `days_back`, the client falls back to the
plain New filter default and shows no window note — tested in DT6.B as a second case.

### DT7 — The model suggests terms that occur word for word

| ID | Criterion | Test |
|---|---|---|
| DT7.A | The system prompt moves from `web/routes_discover.py` to `llm_config.yaml` under `discover.system_prompt` (rule 9). Missing key → logged warning and the built-in text, same pattern as `stop_words`. | `test_dt7a_system_prompt_comes_from_config` (custom prompt in config reaches `client.generate(context=...)`); `test_dt5_repo_config_has_a_discover_block` extended to require `system_prompt` |
| DT7.B | The repo prompt asks for 1–3 word terms that appear word for word in the titles or abstracts given, and says a shorter term or a `*` prefix is preferred over a descriptive phrase. | `test_dt7b_repo_prompt_asks_for_verbatim_short_terms` — asserts the configured prompt contains "1–3 words" (or "1-3 words") and "word for word". This checks the instruction is present; it cannot prove the model obeys it — that is what DT8 measures. |
| DT7.C | Prompt building moves into a pure function `build_discover_prompt(description, papers, max_papers)` in `src/discover.py` (standard L9). Paper text is placed inside a delimited `<papers>…</papers>` block, separate from the instructions (standard L5). | `test_dt7c_prompt_builder_is_pure_and_delimited` — no ctx/network; asserts description and titles present, paper text inside the delimiters, at most `max_papers` papers |
| DT7.D | Behaviour of the route is otherwise unchanged: existing DT1–DT5, gate1, regate1, M2 tests pass unmodified. | existing suite |

### DT8 — Each term shows how many sampled papers contain it

| ID | Criterion | Test |
|---|---|---|
| DT8.A | For each term, the job counts sampled papers whose title or abstract (full abstract, not the 300-char prompt excerpt) contains it, as whole words (`*` = word prefix, commas = alternatives). *Changed after the learning-qa review:* the filter's own `match_term` is a substring test ("aging" matches "imaging"), which would show hits the sources' word search will not find; `match_term` itself is unchanged. Counted over the papers the prompt included (titled ones), reported as `papers_sampled`. | `tests/web/test_discover_routes.py::test_dt8a_term_hits_counted_against_full_abstract` — a paper whose match is past character 300 is still counted |
| DT8.B | Result shape: `terms` stays a list of strings (back-compatible); new `term_hits: {term: n}` and `days_back`. | `test_dt8b_result_shape_is_back_compatible` |
| DT8.C | Adversarial (P7): a term that reads as on-topic but occurs in no sampled paper (e.g. "loneliness intervention outcomes" against papers that say "loneliness" and "intervention" separately) counts 0; its single words count > 0. | `test_dt8c_on_topic_phrase_absent_from_papers_counts_zero` |
| DT8.D | Zero-hit terms are **flagged, not hidden** (P2): the chip shows "0 of 30", gets class `chip-unmatched` (muted, dashed border), and its title says "None of the 30 sampled papers contain this exact wording — it may find nothing." Other chips show "n of 30". | `tests/web/test_frontend_wiring.py::test_dt8d_zero_hit_chip_is_flagged_not_hidden` — node-run `renderDiscoverChips`; asserts every term has a chip, the zero one has the class and title |
| DT8.E | Count line under the chips: "N of M terms occur in the sampled papers." | same test as DT8.D |
| DT8.F | Result without `term_hits` (old job) renders as today, no counts. | `test_dt8f_old_result_renders_without_counts` |

The count is a guide, not a promise: it uses the app's local substring match,
while Europe PMC and arXiv tokenise and stem differently, so a term can count 0
here and still find a paper there, or the reverse. The chip title says
"in the sampled papers", never "will find".

### Regression and housekeeping

| ID | Criterion | Test |
|---|---|---|
| DT-R1 | Every Discover control still exists and works: `discover-desc`, `btn-discover`, chip click adds a group, right-click starts a filter, `chip-on` state. | existing `test_every_element_the_client_uses_exists_in_the_page`, the groupsWithTerm/freeFilterName node tests, plus DT6.B |
| DT-R2 | Retrieval layer unchanged. | `tests/web/test_no_retrieval_drift.py` passes with the baseline **not** advanced |
| DT-R3 | Full suite green locally and in CI after push. | `venv/bin/python -m pytest tests/ -q`; GitHub Actions run checked after push |
| DT-R4 | Real UI check on the deployed app: run Discover with a real description, click a term, confirm the new filter shows 90 days and the chips show counts; run the filter and record the hit count. | **Human/browser check** — cannot be an automated test (live LLM + live sources). Done in the in-app browser against Railway; result recorded in the status report with the actual numbers. |
| DT-S | Spec FP1-B text updated: result shape, window, prompt location. | diff of `docs/web_parity_spec_2026-09-17.md`; rows added to `docs/spec_coverage_webapp.md` |

**Untested by design:** whether the live model follows the DT7.B instruction.
DT8 makes the outcome visible per run instead.

---

## 2. Build order

1. **DT8.C, DT8.A** tests first (the highest-value guard — the count is what
   tells the owner a term is dead), then `count_term_hits(terms, papers)` in
   `src/discover.py`, then wire into `_run_discover` (DT8.B).
2. **DT6.A** — add `days_back` to the result (one line, same change as step 1).
3. **DT7.C → DT7.A → DT7.B** — move prompt building into `src/discover.py`,
   move the system prompt to config, rewrite its text.
4. **DT6.B–D, DT8.D–F** — page: pass `days_back` into `createFilterFromTerm`,
   `windowNote`, chip counts, `chip-unmatched` style.
5. DT-S docs, full suite, `learning-qa` review of the diff, commit, push,
   check CI (DT-R3), then DT-R4 browser check on production.

Steps 1–3 are server-only and testable alone; step 4 depends on the result
shape from steps 1–2.

---

## 3. Owner decisions (defaults chosen; say if you want otherwise)

- **Flag vs hide zero-hit terms:** flag (DT8.D). Hiding would silently drop
  model output (P2).
- **Open filter's window:** leave it, say so in the notice (DT6.C). Changing a
  saved filter's window on a term click would be an unseen side effect.
- **Term length:** 1–3 words (DT7.B). Single words alone broaden too far for
  most topics.

---

## 4. Adjacent issues found, not fixed

- The Search tab's ad hoc box (`q-both`) also quotes a multi-word entry as one
  exact phrase. Typing `social isolation older adults` there finds what the
  exact phrase finds. A hint under the box ("a phrase is matched exactly; use
  commas for alternatives") would help; not in this plan.
- There is no way to express "all of these words, anywhere" inside one text
  group — fields AND together, terms within a field OR. Adding it would change
  `query_builder.py` (protected). Not in this plan.
- `addTermAsGroup` adds the term as an OR group, so each added term widens the
  filter. That is intended (spec FP1-B) and unchanged here.
