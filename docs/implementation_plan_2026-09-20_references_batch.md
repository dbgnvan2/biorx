# Implementation plan — Saved References batch operations and token tracking

**Date:** 2026-09-20
**Status:** awaiting approval — no implementation code written yet
**Requested by:** Dave, 2026-09-19

Six enhancements to the Saved References tab, plus the token accounting three of
them depend on. Written against the current code; every file and function named
below was read, not assumed.

---

## Decisions taken (answered 2026-09-19)

| Question | Answer |
|---|---|
| What is "Review Checked Papers"? | **One synthesis across all checked papers** — a single LLM call producing one combined document (themes, disagreements, gaps). Not a per-paper pass. |
| How should the pre-run cost estimate work? | **Rough range, shown instantly**, from typical paper length. No fetching before the number appears. Labelled as an estimate. |
| What does the menu-bar counter show? | **This session only** — tokens since sign-in. |
| What is the DOC format? | **RTF**, written by hand. No new dependency. |

---

## What exists today (the starting point)

- **References tab** — [`web/static/index.html`](../web/static/index.html) `#panel-references`.
  Per-row checkboxes exist (`#ref-papers-body` rows carry `input[data-item-id]`),
  and `#ref-checked-count` already reports "N selected". Buttons today: Remove
  selected, Download selected PDFs, Download all PDFs, Export CSV, Save summaries (PDF).
- **No Select All on this tab.** The Search tab has one (`#select-all-results`,
  `toggleSelectAll`) — that is the model to follow, including its indeterminate state.
- **Summarize is one paper per action**, a background job:
  `POST /api/summaries` → [`web/routes_summaries.py:298`](../web/routes_summaries.py) →
  `ctx.jobs.submit("summary", …)`, polled at `GET /api/summaries/{job_id}`.
  The client tracks in-flight work in `state.summarizing` ([`app.js:58`](../web/static/app.js)).
  There is **no batch path** and no batch UI.
- **Token usage is not captured anywhere.** `usage_events`
  ([`src/db.py`](../src/db.py)) records `kind, provider, model, key_source, created_at`
  and **no token counts**. Every provider's `generate()` returns only the text and
  discards the `usage` block the API sends: DeepSeek at
  [`llm_providers.py:260`](../src/llm_providers.py) returns
  `data["choices"][0]["message"]["content"]`; Anthropic at
  [`llm_providers.py:335`](../src/llm_providers.py) returns the first text block.
- **Owner-key spend cap** — `_resolve_for` ([`routes_summaries.py:50`](../web/routes_summaries.py))
  reserves a slot per summary against a daily cap, atomically, when billed to the
  owner's key. A user on their own key is uncapped. **Any batch operation must
  reserve per paper through this same path**, or the cap is bypassed.
- **CSV export** — `GET /api/references/{list_id}/export.csv`
  ([`routes_references.py:114`](../web/routes_references.py)), with formula-injection
  guarding and `_safe_filename` for the header.
- **PDF generation** — `fpdf` is already a dependency; `src/summary_pdf.py` renders
  a list's summaries, handles Unicode fonts and counts unrenderable characters.

---

## Module layout

Two new modules rather than growing existing ones (`standards/file-maintainability.md`):

- `src/tokens.py` — token estimation, per-model rates, usage recording/aggregation.
- `src/reference_export.py` — the CSV/RTF/PDF renderers behind one save endpoint.

`web/routes_references.py` and `web/routes_summaries.py` gain routes; `src/llm_providers.py`
changes return shape (see T1.1 — this is the riskiest edit in the batch).

---

## M1 — Token accounting (foundation)

Items 4, 5 and the synthesis cost line all depend on this. **Build it first.**

### M1.A — Capture what the providers already tell us

- **M1.A.1** `generate()` returns text *and* usage. Add a `TokenUsage` dataclass
  (`prompt`, `completion`, `total`, `counted: bool`) in `src/tokens.py`. Each provider
  returns `(text, usage)`; a provider that reports nothing returns `counted=False`
  rather than a zero that would read as "free" (P2 — surface, never silently drop).
  - DeepSeek: `data["usage"]["prompt_tokens"] / ["completion_tokens"]`, guarded the
    same way the content extraction is — a missing `usage` key is `counted=False`, not an error.
  - Anthropic: `response.usage.input_tokens / output_tokens`.
  - Ollama (`src/llm.py`): `prompt_eval_count` / `eval_count`.
  - *Test:* `tests/test_tokens.py::test_m1a1_each_provider_reports_its_usage`
    (fixture responses per provider) and `::test_m1a1_missing_usage_is_not_zero`.
- **M1.A.2** Every caller of `generate()` / `summarize_paper()` is updated. This is a
  breaking signature change across `src/llm_providers.py`, `src/llm.py`, `src/discover.py`,
  `web/routes_summaries.py` and `agents/summarization_agent.py`.
  - *Test:* `tests/test_tokens.py::test_m1a2_no_caller_discards_usage` — a source-level
    scan asserting no call site drops the second element (P25: a wiring check that
    fails before a push, not only in a browser).

### M1.B — Store it

- **M1.B.1** Additive migration in `_run_migrations`: `usage_events` gains
  `prompt_tokens INTEGER DEFAULT 0`, `completion_tokens INTEGER DEFAULT 0`,
  `tokens_counted INTEGER DEFAULT 0` (0 = the provider did not say). Uses the existing
  `_add_column_if_missing`, so old rows are untouched and read as uncounted.
  - *Test:* `tests/test_tokens.py::test_m1b1_migration_is_additive_and_idempotent`,
    run twice against a database seeded with pre-migration rows.
- **M1.B.2** `user_store.record_usage_tokens(db, usage_id, usage)` fills in the row the
  cap already reserved, rather than inserting a second row — otherwise the cap
  double-counts. For a user on their own key (no reservation, `usage_id is None`),
  insert a usage row with `key_source="user"`.
  - *Test:* `::test_m1b2_owner_run_updates_the_reserved_row_not_a_new_one` —
    asserts the row count is unchanged and the cap still reads one slot used.
  - *Test:* `::test_m1b3_user_key_run_is_recorded_without_consuming_the_cap` (P7-style
    adversarial: a run that must be counted for display but must not count against the cap).
- **M1.B.3** A failed run records the tokens actually spent. A model call that
  returns and then fails validation has still been billed.
  - *Test:* `::test_m1b3_failed_summary_still_records_its_tokens`.

### M1.C — Session counter in the menu bar (item 5)

- **M1.C.1** `GET /api/usage/session` returns `{prompt, completion, total, counted_calls,
  uncounted_calls}` for this user since the current session began. "Since sign-in" is
  the session cookie's issue time, already available server-side — no new state.
  - *Test:* `tests/web/test_usage_routes.py::test_m1c1_session_total_excludes_earlier_sessions`.
  - *Test:* `::test_m1c1_another_users_tokens_are_never_included` (authz).
- **M1.C.2** A `#token-meter` in the app header, right-aligned, reading e.g.
  `⏣ 18.4k tokens this session`. Refreshed after any operation that spends tokens.
  With uncounted calls present it says so (`18.4k + 2 uncounted`) rather than
  implying the number is complete.
  - *Test:* `tests/web/test_frontend_wiring.py::test_m1c2_token_meter_is_in_the_header`
    and `::test_m1c2_meter_formatting` (node-run pure function: 0, 947, 18_432, 2_300_000,
    and the uncounted-suffix case).
- **M1.C.3** The header is `position: sticky`-adjacent to the tab bar shipped in
  `cd07d7e`; the meter must not overlap it at phone width.
  - **Cannot be verified by the headless suite.** *Human review proposal:* a browser
    check at 375px and 1440px during the verification pass, recorded in the gate file.
    This is the same limitation TODO already records for the 52px clearance.

---

## M2 — Select All on the References tab (item 1)

- **M2.A.1** A `#select-all-refs` checkbox in the References table header, mirroring
  `#select-all-results`: checking it checks every row, unchecking clears, and it shows
  the indeterminate state when some but not all rows are checked.
  - *Test:* `tests/web/test_frontend_wiring.py::test_m2a1_select_all_refs_toggles_every_row`
    (node-run over the existing DOM harness).
  - *Test:* `::test_m2a1_partial_selection_is_indeterminate`.
- **M2.A.2** Select-all applies to the **whole list**, not just rendered rows, and
  `#ref-checked-count` reports the real number. If the list is ever paginated, the
  count must say what it covers (P2).
  - *Test:* `::test_m2a2_count_matches_the_selection`.

---

## M3 — Summarize Checked (item 2)

- **M3.A.1** A "Summarize checked" button on the References tab, disabled with nothing
  checked. It runs the **existing per-paper job path once per checked paper**, sequentially,
  so the owner-key cap is reserved per paper exactly as today. No new server batch endpoint
  — a batch endpoint would need its own cap logic, and duplicated cap logic is how caps
  get bypassed.
  - *Test:* `tests/web/test_summaries_routes.py::test_m3a1_batch_reserves_one_slot_per_paper`.
  - *Test:* `::test_m3a2_batch_stops_at_the_cap_and_says_where_it_stopped` — hitting the
    cap mid-run reports "summarized 4 of 9; the daily cap on the shared key stopped the
    rest", and the 4 are kept (P2: surface what was dropped, "N of M").
- **M3.A.2** Papers already summarized are skipped, and the skip is reported, not silent.
  - *Test:* `::test_m3a2_already_summarized_are_skipped_and_counted`.
- **M3.A.3** Progress is visible per paper and the run can be stopped; a stopped run
  reports what completed. A failure on one paper does not abandon the rest (P1:
  transient ≠ terminal).
  - *Test:* `tests/web/test_frontend_wiring.py::test_m3a3_one_failure_does_not_end_the_batch`
    (node-run, mirroring the existing `test_fr4_4_*` pattern).

---

## M4 — Review Checked Papers (item 3)

One synthesis across the checked papers, per the decision above.

- **M4.A.1** `POST /api/reviews` takes a list id plus item ids, and submits a job
  (`ctx.jobs.submit("review", …)`) that builds one prompt from the checked papers'
  **stored summaries**, falling back to abstracts where no summary exists. Which
  papers contributed, and on what basis, is recorded with the result — a synthesis
  that silently drops half its inputs is a fabrication risk.
  - *Test:* `tests/web/test_review_routes.py::test_m4a1_synthesis_uses_summaries_then_abstracts`.
  - *Test:* `::test_m4a1_result_records_which_papers_contributed`.
  - *Test:* `::test_m4a2_a_paper_with_neither_is_excluded_and_reported` (P2).
- **M4.A.2** The synthesis prompt is built from stored text only. **No new fetching**,
  so the cost is knowable up front (this is what makes M5's estimate honest for reviews).
  - *Test:* `::test_m4a2_no_network_call_is_made` (the finder hook is asserted untouched).
- **M4.A.3** The review is stored so it survives a reload, in a new `reviews` table
  (`id, user_id, list_id, item_ids_json, review_text, model_version, prompt_tokens,
  completion_tokens, created_at`), and is shown on the References tab with a
  "Save review" action that routes into M6's exporter.
  - *Test:* `::test_m4a3_a_stored_review_is_returned_on_reload`.
  - *Test:* `::test_m4a3_reviews_are_per_user` (authz — a list id from another user is 404).
- **M4.A.4** The combined prompt can exceed the model's context window on a large
  selection. It is capped, and **what was cut is stated in the result**, never trimmed
  silently (P2/P9). The cap is config, not a literal (P4 — `llm_config.yaml`).
  - *Test:* `::test_m4a4_oversized_selection_is_capped_and_says_so`, with a real-scale
    fixture big enough that the cap actually bites (P9).

---

## M5 — Cost estimate before spending (item 4)

- **M5.A.1** `src/tokens.py::estimate_summary_tokens(n_papers)` returns a **range**
  from a configured typical-paper length (`llm_config.yaml`, not a literal — P4):
  low/high token counts and a dollar range at the configured per-model rate.
  - *Test:* `tests/test_tokens.py::test_m5a1_estimate_scales_and_is_a_range`.
  - *Test:* `::test_m5a1_rates_come_from_config_not_source` (P4 — a source scan asserting
    no hardcoded rate).
- **M5.A.2** `estimate_review_tokens(items)` is computed from the **actual stored text**
  the prompt will contain, so the review estimate is near-exact, not a guess. The two
  estimates are presented differently and labelled honestly: a range for summarize, a
  figure for review.
  - *Test:* `::test_m5a2_review_estimate_is_derived_from_the_real_prompt_text`.
- **M5.A.3** Both "Summarize checked" and "Review checked" show a confirm dialog with the
  estimate, the paper count, which key pays (owner's shared key vs the user's own), and
  the remaining daily cap when on the shared key. Nothing is spent before confirmation.
  - *Test:* `tests/web/test_frontend_wiring.py::test_m5a3_no_request_leaves_before_confirm`
    (node-run; the POST must not have fired).
  - *Test:* `::test_m5a3_dialog_states_whose_key_pays`.
- **M5.A.4** The estimate is labelled an estimate wherever it appears, and after the run
  the **actual** token count is shown next to it. An estimate presented as a fact is the
  failure mode this item exists to prevent.
  - *Test:* `::test_m5a4_actual_is_shown_after_the_run`.

---

## M6 — Save Reference List: CSV / RTF / PDF (item 6)

- **M6.A.1** `GET /api/references/{list_id}/export.csv` is **kept working** (it is a URL
  users may have bookmarked) and a new `GET /api/references/{list_id}/save?format=csv|rtf|pdf`
  is added, with the three renderers in `src/reference_export.py`. The existing CSV
  formula-injection guard and `_safe_filename` apply to all three.
  - *Test:* `tests/web/test_reference_export.py::test_m6a1_csv_output_is_unchanged`
    (byte-identical to the current endpoint for the same list — a refactor guard, P12).
  - *Test:* `::test_m6a1_unknown_format_is_refused` (400, not a silent default).
- **M6.A.2** RTF is written by hand: a minimal valid document, non-ASCII escaped as
  `\uN?` (paper titles carry Greek and accented characters), braces and backslashes
  escaped. Opens in Word and Pages.
  - *Test:* `::test_m6a2_rtf_escapes_unicode_and_control_characters`, with a Greek/accented
    fixture title.
  - *Test:* `::test_m6a2_rtf_is_structurally_valid` (balanced braces, required header).
  - **Not covered by the suite:** that Word and Pages actually render it.
    *Human review proposal:* open one exported file in both during verification and
    record it in the gate file.
- **M6.A.3** PDF reuses `src/summary_pdf.py`'s font handling, including its
  unrenderable-character count, rather than a second font implementation (P5 — fix the
  class, not the instance).
  - *Test:* `::test_m6a3_pdf_reports_unrenderable_characters`.
- **M6.A.4** The button is renamed "Save Reference List" with a format choice.
  "Export CSV" disappears from the UI — an explicitly approved rename
  (`standards/ui-regression.md` rule 2).
  - *Test:* `tests/web/test_frontend_wiring.py::test_m6a4_save_button_offers_three_formats`.
- **M6.A.5** Downloaded files are named after the list, not an id — the same defect
  already fixed for PDFs in `fb53149`.
  - *Test:* `::test_m6a5_saved_file_is_named_after_the_list`.

---

## Implementation order

M1 first: M2–M6 either display its numbers or depend on its capture.

1. **M1.A** provider usage capture — *the riskiest change in the batch*; it alters a
   signature used in five modules. Land it alone, with the full suite green, before anything else.
2. **M1.B** migration + recording.
3. **M1.C** session endpoint + header meter. **Item 5 is now delivered.**
4. **M2** Select All — independent of everything else; could land in parallel.
5. **M5.A.1/A.2** estimators (pure functions, no UI).
6. **M3** Summarize checked, wired to the M5 confirm dialog. **Items 1, 2, 4 delivered.**
7. **M4** Review — the largest single piece; new table, new job type, new prompt.
   **Item 3 delivered.**
8. **M6** the exporter. **Item 6 delivered.**

Each numbered step is a commit with its tests green. Steps 3, 6, 7 and 8 each complete a
requested item, so the batch can stop at any of them with something coherent shipped.

---

## Risks and things I will not pretend are solved

1. **M1.A changes a signature across five modules.** The compensating control is
   M1.A.2's source scan plus the full suite; there is no type checker in this repo to
   catch a missed call site. This is why it lands alone and first.
2. **Cost estimates for summaries can be wrong by roughly 2x.** A 60-page paper with
   supplementary material is nothing like a 6-page letter. The range and the "estimate"
   label are the mitigation; M5.A.4's actual-after-the-fact figure is the check.
3. **Ollama reports tokens differently and may report nothing.** Those runs show as
   uncounted rather than free.
4. **Three criteria are not code-testable** — M1.C.3 (header layout at phone width),
   M6.A.2 (Word/Pages actually opening the RTF), and the existing 52px clearance from
   the last batch. Each has a human-review proposal above; all three should be checked
   in one browser pass and recorded in the gate file.
5. **A synthesis over papers whose summaries are stale or abstract-only** will read as
   more authoritative than its inputs warrant. M4.A.1's contribution record is what makes
   that visible; it must appear in the output, not only in the database.

---

## Acceptance criteria summary

| ID | Criterion | Verified by |
|---|---|---|
| M1.A.1 | Providers return usage; unreported ≠ zero | `test_m1a1_each_provider_reports_its_usage`, `test_m1a1_missing_usage_is_not_zero` |
| M1.A.2 | No caller discards usage | `test_m1a2_no_caller_discards_usage` |
| M1.B.1 | Migration additive and idempotent | `test_m1b1_migration_is_additive_and_idempotent` |
| M1.B.2 | Owner run updates the reserved row | `test_m1b2_owner_run_updates_the_reserved_row_not_a_new_one` |
| M1.B.3 | Failed run still records tokens | `test_m1b3_failed_summary_still_records_its_tokens` |
| M1.C.1 | Session total is this session, this user | `test_m1c1_session_total_excludes_earlier_sessions`, `test_m1c1_another_users_tokens_are_never_included` |
| M1.C.2 | Meter present and formatted | `test_m1c2_token_meter_is_in_the_header`, `test_m1c2_meter_formatting` |
| M1.C.3 | Meter does not collide at phone width | **human review** — browser pass |
| M2.A.1 | Select All toggles all rows, indeterminate when partial | `test_m2a1_select_all_refs_toggles_every_row`, `test_m2a1_partial_selection_is_indeterminate` |
| M2.A.2 | Count matches the selection | `test_m2a2_count_matches_the_selection` |
| M3.A.1 | One cap slot per paper | `test_m3a1_batch_reserves_one_slot_per_paper` |
| M3.A.2 | Cap stop and skips are reported as "N of M" | `test_m3a2_batch_stops_at_the_cap_and_says_where_it_stopped`, `test_m3a2_already_summarized_are_skipped_and_counted` |
| M3.A.3 | One failure does not end the batch | `test_m3a3_one_failure_does_not_end_the_batch` |
| M4.A.1 | Synthesis uses summaries then abstracts; contributors recorded | `test_m4a1_synthesis_uses_summaries_then_abstracts`, `test_m4a1_result_records_which_papers_contributed` |
| M4.A.2 | No fetching during review | `test_m4a2_no_network_call_is_made`, `test_m4a2_a_paper_with_neither_is_excluded_and_reported` |
| M4.A.3 | Review persists; per-user | `test_m4a3_a_stored_review_is_returned_on_reload`, `test_m4a3_reviews_are_per_user` |
| M4.A.4 | Oversized selection capped and stated | `test_m4a4_oversized_selection_is_capped_and_says_so` |
| M5.A.1 | Estimate is a range, rates from config | `test_m5a1_estimate_scales_and_is_a_range`, `test_m5a1_rates_come_from_config_not_source` |
| M5.A.2 | Review estimate from real prompt text | `test_m5a2_review_estimate_is_derived_from_the_real_prompt_text` |
| M5.A.3 | Nothing spent before confirmation; key stated | `test_m5a3_no_request_leaves_before_confirm`, `test_m5a3_dialog_states_whose_key_pays` |
| M5.A.4 | Actual shown after the run | `test_m5a4_actual_is_shown_after_the_run` |
| M6.A.1 | CSV unchanged; bad format refused | `test_m6a1_csv_output_is_unchanged`, `test_m6a1_unknown_format_is_refused` |
| M6.A.2 | RTF escaping and structure | `test_m6a2_rtf_escapes_unicode_and_control_characters`, `test_m6a2_rtf_is_structurally_valid` |
| M6.A.2 | RTF opens in Word and Pages | **human review** — open one file in each |
| M6.A.3 | PDF reports unrenderable characters | `test_m6a3_pdf_reports_unrenderable_characters` |
| M6.A.4 | Three formats offered | `test_m6a4_save_button_offers_three_formats` |
| M6.A.5 | Saved file named after the list | `test_m6a5_saved_file_is_named_after_the_list` |

28 code-tested criteria, 2 needing human review.

---

## Out of scope, flagged not fixed

Adjacent problems found while reading the code for this plan
(global rule 10 — flag, do not silently fix or silently leave):

- **The desktop GUI and the web app keep reference lists in different tables**
  (`reference_lists` vs `user_reference_lists`, [`src/db.py:358`](../src/db.py)). Lists
  saved in one are invisible in the other. Deliberate, but it is why a list saved on
  Railway cannot be found in the local desktop app. No migration path exists.
- **`~/preprints/summaries/` is created but never written to.** Dead directory.
- **`usage_events` has no retention policy.** With tokens recorded per call it will grow
  without bound on the Railway volume.
- **`bookmarks` and `search_history` are empty and unreferenced by the web app** — dead
  schema, or an unfinished feature.
