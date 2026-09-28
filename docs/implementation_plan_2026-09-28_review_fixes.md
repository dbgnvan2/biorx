# Implementation plan — fixes for REVIEW-biorx-2026-09-28

**Request:** chat, 2026-09-28: "create plan to fix all the findings".
**Source of findings:** `REVIEW-biorx-2026-09-28.md`. Finding IDs (B1, A1, S1, M13, T2 …) are used verbatim as spec IDs throughout. Every test name contains its ID, e.g. `test_b1_skipped_source_is_reported`.
**Status:** PLAN — awaiting approval. No code has been written.
**Surface:** the web app, shared `src/` code, and `agents/monitor.py`. `gui.py` is not changed. It is retiring, and any shared-code change that breaks it is recorded as a known GUI regression, not fixed.

---

## 0. Decisions (from chat, 2026-09-28)

| ID | Question | Decision |
|---|---|---|
| D1 | How shared summaries are protected (A1) | **Shared, server-verified.** Keep one summary per paper. The server re-resolves paper metadata itself and never stores what the browser sends. An abstract stand-in never replaces a full-text summary. An existing full-text summary is returned, not re-billed. |
| D2 | CLI agents | **Keep `monitor.py` only.** Delete `agents/search_agent.py`, `key_terms.json`, the legacy `run.sh` modes and `test_components.py` references. `agents/summarization_agent.py` is kept only as a thin CLI over the new shared `src/summarize.py` (S1). If that proves more than thin, it is deleted instead and this is reported. |
| D3 | Where `monitor.py --download-dir` runs | **Only on the owner's Mac.** Route it through `safe_fetch` for the size cap and PDF check (M24, minor). |
| D4 | Supported Python | **3.12 only.** Update README.md and CLAUDE.md. No 3.9 matrix. |

Defaults chosen by me, which you can change at approval:

| ID | Question | Default |
|---|---|---|
| D5 | Should a published article and its preprint merge in dedup (M22)? | Merge behaviour is unchanged. The fix only makes the surname key consistent so the existing rule works as designed. `is_preprint` stays sticky. |
| D6 | Should owner-key spend from the CLI be metered? | No. The CLI runs on the owner's Mac with the owner's key. `summarize.py` takes an optional spend hook, and only the web route passes one. |
| D7 | Retrieval drift guard (`tests/web/test_no_retrieval_drift.py`, W1.a) | Batch 1 changes retrieval logic on purpose. Its final commit moves the baseline, with one comment line per changed file naming the finding IDs, as the file's history already does. |

---

## 1. Order and dependencies

The rules applied: write the test for the highest-impact, most-likely-to-regress fix first; build shared pieces before their users; put the front end after the server endpoints it calls.

| Batch | Contents | Depends on | Why this position |
|---|---|---|---|
| 1 | Retrieval correctness: B1, B2, B3, B4, S3, B5, B6, B7, M18–M22, M31 | — | These silently give wrong search answers, and the blocker B1 is here. |
| 2 | Shared summary integrity + one summarize pipeline: A1, M26, A8, S1, A13, A10, M33, M29, M27, M28, A11, A12, M25 | — | The other blocker (A1). S1 is the shared function the CLI rebuild needs. |
| 3 | Filters and run preconditions: A2, S2, B13, M2, S4 | 1 (S3 vocabulary for normalisation) | Data loss (A2), and one place for run preconditions. |
| 4 | Jobs and spend: M12, A14, A7 (server), M1, M11 | 2 (summarize.py) | Spend settlement is extracted once, then the fixes use it. |
| 5 | Front end: A3, A4, A5, A6, A7 (client), M13–M17 | 4 (A7 endpoint), 3 (409 on name clash) | Calls endpoints added in 3 and 4. |
| 6 | Bounding hostile input + dependencies: B8, B9, B10, M24, M23 | — | Independent, and it can run in parallel with 3–5. |
| 7 | Auth hardening: M4, M5, M6, M7, M8, M9, M10 | — | Independent. |
| 8 | Tests, CI, docs and cleanup: T1, T2, M35–M39, M30, M32, M34, D4 docs, LEARNINGS.md | all | T1/T2 can move earlier if you prefer. M35 needs the node harness from Batch 5. |

Each batch is a separate commit or set of commits. It goes through the `/csdp` sweep (learning-qa + code-review) and a real-UI run before the next batch starts. The real-UI run is the LEARNINGS common-flow check: an empty filter, a filter that matches nothing, and one that matches.

---

## 2. Batch 1 — retrieval correctness

**Preliminary check (no code).** Run the saved "Inflammation" and "Loneliness" filters once against the live sources through `monitor.py --dry-run --json`, and save the per-source counts to `docs/cycles/2026-09-28_retrieval_baseline.json`. The same run is repeated after Batch 1, so the effect is measured on real data rather than claimed.

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| B1 | `orchestrator.search`: the budget applies **per source** (`max_results` per source, with a separate overall cap of `max_results × enabled sources`, whose limit is set in `sources_config.yaml`). Any source skipped or truncated (by budget or by `MAX_PAGES_PER_SOURCE`) emits an `on_status` line containing `FAILURE_STATUS_MARKER` and the words "not searched (result limit reached)" or "truncated at N". | (a) With source 1 returning more than `max_results` records, source 2 is still queried. (b) Any source not queried or truncated produces a marked status line. (c) monitor.py exits 2 on a truncated source, as it does for other partial results. | `tests/test_orchestrator.py::test_b1_first_source_cannot_starve_later_sources`, `::test_b1_truncated_source_is_reported`, `tests/test_monitor.py::test_b1_truncation_sets_exit_2` |
| B2 | `get_date_range` treats `""` and `None` as missing (`or` instead of the `.get` default). | Empty-string dates with `days_back=7` give a 7-day window to arXiv and to OSF. | `tests/test_query_builder.py::test_b2_empty_string_dates_use_days_back_arxiv`, `::test_b2_empty_string_dates_use_days_back_osf` |
| B3 | PsyArXiv/SocArXiv: one OSF request per distinct title term (capped by `osf_max_terms` in `sources_config.yaml`; terms over the cap are **reported**, not dropped silently), merged by id. Multi-word terms are sent whole. | Both terms of a two-group filter reach the requests. A multi-word term is not cut down to its first word. Terms past the cap produce a status line. | `tests/test_adapters.py::test_b3_every_or_group_reaches_osf`, `::test_b3_multiword_term_sent_whole`, `::test_b3_terms_over_cap_reported` |
| B4 | A wildcard matches a word prefix: `\b` + escaped prefix. | "Stress in adolescents" matches `adolescen*`. "Nonadolescent" does not (adversarial). | `tests/test_filtering.py::test_b4_wildcard_matches_mid_title`, `::test_b4_wildcard_does_not_match_inside_word` |
| S3 | New `filter_vocabulary.yaml` with stable ids, labels, and per-id match rules against CanonicalRecord fields, plus the species organism list. `src/filtering.py` and `query_builder.py` read it. Served at a new `GET /api/vocabulary`. `app.js` builds its dropdowns from it and stops hard-coding them. Old stored label values are mapped to ids by `normalise_filter`. An unknown value is logged at WARNING, and the filter is **refused with 400 at save time**, not widened silently. | (a) No vocabulary literal remains in `filtering.py`, `query_builder.py` or `app.js`. (b) Every stored filter in `filters.json` still normalises. (c) An unknown facet value is refused on save. | `tests/test_filtering.py::test_s3_vocab_has_no_literals_in_code` (source scan, justified: it is a rule-9 check), `::test_s3_legacy_labels_normalise_to_ids`, `tests/web/test_filters_routes.py::test_s3_unknown_facet_value_refused`, `tests/web/test_frontend_wiring.py::test_s3_dropdowns_built_from_vocabulary` (node harness) |
| B5 | Match rules in the vocabulary map `paper_type` to `document_type` values that adapters actually emit. `institution` is hidden from the UI and refused on save until an adapter provides it. License matching is normalised (`cc_by`, `CC BY`, `cc-by`, license URLs all resolve to one id), and license filtering moves **after** enrichment so Unpaywall/Crossref licences count. | For every vocabulary option, at least one record normalised from some adapter's fixture can match it. A facet no adapter can provide cannot be saved. "Loneliness" with `review article` matches a review record. | `tests/test_filtering.py::test_b5_every_facet_option_matchable_by_some_adapter`, `::test_b5_license_forms_normalise`, `tests/test_orchestrator.py::test_b5_license_filter_sees_enriched_license` |
| B6 | Pagination state is moved off adapter instances. `search()` returns a page plus a cursor/total object that the orchestrator holds per call. `requests.Session` becomes per thread (`threading.local`). arXiv spacing uses a module-level lock. | Two threads running different queries against one orchestrator, with interleaved fake pages, each get their own full, non-repeating result set. | `tests/test_orchestrator.py::test_b6_concurrent_searches_do_not_share_cursor`, `tests/test_arxiv.py::test_b6_spacing_enforced_across_threads` |
| B7 | The bioRxiv adapter uses `get_date_range()` and `search_by_date_range`, queries both `biorxiv` and `medrxiv`, pages each, and tags `server`. | A range filter with `days_back: 0` queries the saved range. medRxiv is requested. Records carry the right server. | `tests/test_adapters.py::test_b7_date_range_used`, `::test_b7_medrxiv_queried` |
| M18 | bioRxiv `version` is passed to the record (the field is renamed `arxiv_version` → `version`, keeping a read alias). | A v2 bioRxiv record reports 2, and the "revised only" filter keeps it. | `tests/test_adapters.py::test_m18_biorxiv_version_carried` |
| M19 | `(msg.get("container-title") or [""])[0]`. Non-adapter exceptions in `_enrich` are logged at WARNING with `exc_info` and counted separately from outages. | An empty container-title does not count as a Crossref failure. | `tests/test_adapters.py::test_m19_empty_container_title`, `tests/test_orchestrator.py::test_m19_code_error_not_counted_as_outage` |
| M20 | Normalisation failures are counted per source, logged at WARNING, and reported through the status line "N of M records from X could not be read". | A source whose records all fail to normalise reports that, not "0 fetched". | `tests/test_orchestrator.py::test_m20_normalize_failures_reported` |
| M21 | Europe PMC `get_by_id`, `fetch_abstract_from_fulltext` and `fetch_openalex_abstract` raise `SourceUnavailableError` on transport errors or 5xx, and return None only on a real not-found. OpenAlex goes through `with_retry`. The failure is added to `AbstractRecovery.failed`. | An outage shows as "Could not reach: Europe PMC", not "nothing found". | `tests/test_paper_meta.py::test_m21_outage_reported_as_failed`, `::test_m21_openalex_retried` |
| M22 | The dedup surname comes from structured last names where the adapter has them (Europe PMC `lastName`, bioRxiv "Last, F."), with punctuation stripped. arXiv records get `doi=10.48550/arXiv.<id>`. | The same paper from Europe PMC ("Smith J") and arXiv ("John Smith") dedups to one record. A different Smith paper with the same year but a different title does not (adversarial). | `tests/test_dedup.py::test_m22_cross_source_surname_match`, `::test_m22_different_title_not_merged` |
| M31 | One `OsfPreprintAdapter(provider, label)` replaces the two copies. Adapters carry `label` and `build_query`, so the orchestrator stops branching on names. `_SOURCE_TRUST` is deleted. An enabled source with no adapter (openalex) produces an orchestrator warning. | The existing PsyArXiv/SocArXiv tests pass against the shared class. `openalex: enabled: true` produces a warning. | existing `tests/test_adapters.py` OSF tests, plus `tests/test_orchestrator.py::test_m31_enabled_source_without_adapter_warns` |
| D7 | Drift baseline moved, with the reasons listed. | `test_no_retrieval_drift` passes at the batch's last commit. | `tests/web/test_no_retrieval_drift.py` (existing) |

**Human check (not code-testable):** re-run the preliminary live filters and compare the per-source counts with the baseline file. Live OSF and arXiv reactions to the new params can only be checked this way. The comparison is recorded in the batch's gate file.

---

## 3. Batch 2 — shared summary integrity and one summarize pipeline

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| S1 | New `src/summarize.py` with `summarize_paper(db, paper_ref, client, *, spend_hook=None) -> SummaryOutcome`. It finds text, recovers the abstract, calls the model, coerces the result, and stores it under one `summary_text` convention (`""` when structured fields exist). `routes_summaries` and the CLI both call it. `_coerce_summary` becomes public. | The route and the CLI produce identical rows for the same fake paper and fake model. | `tests/test_summarize.py::test_s1_route_and_cli_store_identical_rows` |
| A1 | `summarize_paper` takes a **paper reference** (DOI or canonical_id), not a dict. The server resolves it in this order: stored row, then the adapter `get_by_id` chosen by the canonical_id prefix, then Crossref by DOI. The resolved record is the only source of title, abstract and URLs. If resolution fails transiently, the route returns 503 "try again" (P1) and stores nothing. If the paper cannot be resolved at all, it returns 404 and stores nothing. `POST /api/summaries` ignores all client paper fields except the reference. | (a) A request with a real DOI and an invented title or abstract stores the **resolved** metadata, never the client text (adversarial). (b) A client-supplied `pdf_url` is never fetched. (c) A resolver outage returns 503 and writes no row. | `tests/web/test_summaries_routes.py::test_a1_client_abstract_never_stored`, `::test_a1_client_pdf_url_ignored`, `::test_a1_resolver_outage_is_503_and_writes_nothing` |
| A1 / M26 | `insert_summary` refuses to replace a `full_text` row with an `abstract` row (it raises `SummaryDowngradeRefused`). `summarize_paper` returns an existing full-text summary without calling the model. | (a) An existing full-text summary survives an abstract-only run, from both the route and the CLI. (b) No model call and no spend for a paper that already has a full-text summary. | `tests/test_db_summaries.py::test_a1_abstract_cannot_replace_full_text`, `tests/web/test_summaries_routes.py::test_a1_existing_full_text_returned_not_billed`, `tests/test_summarize.py::test_m26_cli_path_cannot_downgrade` |
| A8 | `insert_summary` raises `SummaryNotSaved` instead of returning None. A `None` paper row is also an error. The route reports job status `done` with `saved: false` and a reason, and the UI shows "shown but not stored — it will be charged again if you re-run". The spend is still recorded (the provider was called). | A locked database gives `saved: false` and a message. The CLI exits non-zero for that paper. | `tests/web/test_summaries_routes.py::test_a8_db_error_reports_not_saved`, `tests/test_summarize.py::test_a8_cli_reports_failure` |
| A13 | Fixed by the S1 convention. `review._text_for` also uses `summary_text` only when the structured fields are empty. Existing CLI rows are fixed by a **code-path migration** in `db.py` (it clears `summary_text` where the structured fields exist and `source_text='full_text'`), not by hand edits. | A CLI-style row appears once in the review prompt. The migration is idempotent. | `tests/test_review.py::test_a13_summary_not_sent_twice`, `tests/test_db_migrations.py::test_a13_migration_clears_duplicate_text` |
| A10 / M33 / M29 | `OllamaClient` uses the shared JSON prompt (`_build_summary_prompt`, `"format": "json"`), the same parse-or-raise path and retry/backoff as its siblings, and raises `LLMError` subclasses instead of returning None. The text parser is deleted. | Malformed Ollama output raises `ProviderResponseError`. A timeout is retried. A non-JSON body is a typed error, not `ValueError`. | `tests/web/test_providers.py::test_a10_ollama_malformed_raises`, `::test_m29_ollama_retried`, `::test_m33_ollama_uses_delimited_prompt` |
| M27 | `insert_paper` treats only `UNIQUE constraint failed` as a duplicate. Any other `IntegrityError` is logged at ERROR and raised. | A NOT NULL failure is not reported as "already exists". | `tests/test_db_summaries.py::test_m27_not_null_is_error_not_duplicate` |
| M28 | `abstract = paper.get("abstract") or ""` in `summarize.py`. | A NULL abstract yields the "neither full text nor abstract" outcome, not a crash. | `tests/test_summarize.py::test_m28_null_abstract` |
| A11 | `get_unsummarized_papers` selects papers with no summary row, or with `source_text='abstract'`. It drops `downloaded`, adds `summary_attempted_at`, orders by oldest attempt, and records attempts. | Papers never downloaded are selected. A paper that always fails does not block others across runs (real-scale: 3 × limit papers, one third failing). | `tests/test_summarize.py::test_a11_selects_undownloaded`, `::test_a11_failing_papers_do_not_starve_others` |
| A12 | `db_path=None` defaults in the kept CLI. | With `DATA_DIR` set, the CLI opens `$DATA_DIR/biorxiv.db`. | `tests/test_summarize.py::test_a12_cli_honours_data_dir` |
| M25 | `--mock` requires `--db-path` and refuses the default database. | `--mock` without `--db-path` exits non-zero before touching any DB. | `tests/test_summarize.py::test_m25_mock_refuses_default_db` |

**Question to settle in the batch, not now:** if `summarization_agent.py` on top of `summarize.py` is more than about 60 lines, it is deleted instead (per D2) and this is reported.

---

## 4. Batch 3 — filters and run preconditions

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| A2 | `update_filter` and `create_filter` return **409** "A filter called X already exists" when the name, compared case-insensitively, belongs to another id. `user_store.upsert_filter` is split into `insert_filter` / `update_filter`, so there is no ON CONFLICT overwrite. | Renaming A onto B returns 409, and both filters are unchanged. POST with an existing name returns 409. Renaming to your own name with only a case change succeeds. | `tests/web/test_filters_routes.py::test_a2_rename_onto_existing_is_409`, `::test_a2_create_duplicate_is_409`, `::test_a2_case_only_rename_of_self_ok` |
| S2 / B13 | `orchestrator.search` normalises the filter, raises `EmptyFilterError` on an empty one, and reports source failures through a structured `on_source_failure(source, kind, detail)`. Web and monitor use this and stop parsing status strings. `_SOURCE_LABELS` gets a public `source_label()`. | (a) monitor.py and the web give the same queries for a legacy-shaped filter. (b) Neither imports `_SOURCE_LABELS`. (c) Changing status wording does not change failure counts. | `tests/test_orchestrator.py::test_s2_empty_filter_refused_in_engine`, `tests/test_monitor.py::test_b13_legacy_filter_normalised`, `tests/test_orchestrator.py::test_s2_failures_structured_not_parsed` |
| M2 | Discover strips its description and relies on the engine's empty-filter refusal (400 before any slot is reserved). It passes `enrich_only=lambda r: False`. | A whitespace description returns 400 with no reservation. Enrichment adapters are not called. | `tests/web/test_discover_routes.py::test_m2_whitespace_description_400`, `::test_m2_no_enrichment` |
| S4 | Delete `agents/search_agent.py`, `key_terms.json`, `run.sh search`/`summarize`/`test` legacy modes (`run.sh search` now calls `monitor.py --all`), and the docs references (README, QUICKSTART, CLAUDE.md MVP list). | None of these files exist, and no import of them remains. `run.sh search` calls monitor. | `tests/web/test_deploy_files.py::test_s4_legacy_agent_removed`, `::test_s4_run_sh_search_uses_monitor` |

---

## 5. Batch 4 — jobs and spend

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| M12 | New `src/spend.py`: `admit(ctx, user, kind)` raises 429/400/503 as domain errors, and `with billed_call(...) as call:` records spend, or releases the slot if the provider was never called, and releases the DB connection. Summaries, discover and reviews all use it. Workers raise domain errors, not `HTTPException`. | No `release_usage` or `record_spend` call remains outside `spend.py`. The three routes return identical status codes for the same failure. | `tests/web/test_spend.py::test_m12_single_settlement_path` (import scan plus behaviour), `::test_m12_routes_agree_on_status_codes` (parametrised across the 3 routes) |
| A14 (b) | `JobRegistry.submit(on_never_ran=...)` runs when `_run` sees a job cancelled before start, and when `submit` raises. `billed_call` registers the slot release there. | After `shutdown()` with queued owner-key jobs, `owner_usage_today` is 0. A `submit` failure releases the slot. | `tests/web/test_jobs.py::test_a14_cancelled_before_start_releases_slot`, `::test_a14_submit_failure_releases_slot` |
| A14 (a) | Two pools: `search` (2 workers) and `model` (3 workers), sizes in `llm_config.yaml`/env. One running search per user, with a second refused 409. Finished jobs expire on `lookup()` too. | A user's second concurrent search gets 409. A summary starts while 2 searches block the search pool. | `tests/web/test_jobs.py::test_a14_one_search_per_user`, `::test_a14_model_jobs_not_blocked_by_searches`, `::test_a14_expiry_on_lookup` |
| A7 (server) | A summary job is keyed by (user, paper ref). A second request while one runs returns 409 with the running `job_id`. New `GET /api/jobs/running` lists the user's running jobs (kind, paper ref, list id, job id). `PROTECTED_ROUTE_COUNT` is updated. | A duplicate summary request returns 409 plus the existing job id, and no second job is created. The running list shows the job. | `tests/web/test_summaries_routes.py::test_a7_duplicate_job_409`, `tests/web/test_jobs.py::test_a7_running_jobs_listed`, `tests/web/test_auth.py` (count) |
| M1 | `start_review` maps credential errors through `spend.admit`. `review_status` returns 410 "That review has expired — run it again." or 404 "No such job.". | Review with no key returns 400. An expired review returns 410 with that sentence. | `tests/web/test_review_routes.py::test_m1_no_key_is_400`, `::test_m1_expired_is_410` |
| M11 | `Database.summaries_for_papers(ids)` (one `IN` query, chunked at 500) and one `summary_card()` serializer, used in all four places. | Query count for 2,000 papers is at most 5 (counted with a `set_trace_callback`). All four endpoints return identical cards for the same paper. | `tests/web/test_searches_routes.py::test_m11_summaries_bulk_query_count`, `::test_m11_card_identical_across_endpoints` |

---

## 6. Batch 5 — front end

All new behaviour tests run real `app.js` functions in the node harness (`_run_handler` style, with `api()` stubbed), not source substring checks. The helper added here is reused by M35.

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| A3 | `newFilter` resets all six facet selects to `(any)`. | `buildFilterDict()` after `selectFilter(x)` then `newFilter()` has no facet keys. | `tests/web/test_frontend_wiring.py::test_a3_new_filter_resets_facets` |
| A4 | `loadFilterTab` redraws defaults only when no filter is open. Otherwise it calls `selectFilter(activeFilterId)`. | Open a filter with 1 source, switch tabs, and save: the PUT carries that 1 source. | `::test_a4_tab_return_keeps_filter_sources` |
| A5 | `signOut` and the 401 path call `clearLocalSettings()`. Storage is keyed by user id, and the old key name is migrated once for the signed-in user. | After `signOut`, no `biorx_local_key*` entry remains. User B never reads user A's entry. | `::test_a5_sign_out_clears_key`, `::test_a5_key_scoped_to_user` |
| A6 | Every poller captures its job id and drops responses for another id. There is an in-flight flag, `api()` gets an `AbortController` timeout, and the Test button disables while running. | A late done-response for job A does not stop or finish job B (search and filter test). | `::test_a6_stale_search_response_ignored`, `::test_a6_stale_filter_test_response_ignored` |
| A7 (client) | On boot, call `/api/jobs/running`, mark matching buttons busy, and resume polling. A 409 on start adopts the returned job id. | With a running summary job, the Summarize button is disabled after a reload. A 409 attaches to the existing job. | `::test_a7_busy_state_restored_on_boot`, `::test_a7_409_adopts_running_job` |
| M13 | Batch summarize adds each paper to `state.summarizing`. The row button checks it. | During a batch, a row Summarize click makes no API call. | `::test_m13_row_click_blocked_during_batch` |
| M14 | Stale-list checks after the items fetch and on review completion. | List A's late response does not render under list B. | `::test_m14_stale_list_response_ignored` |
| M15 | The page buttons catch errors, restore the offset, and show a notice. | A 410 on Next restores the offset and shows a message. | `::test_m15_page_error_restores_offset` |
| M16 | The Discover poll uses `shouldStopPolling` and resets `discoverPolling`. | One status-0 failure does not stop polling. | `::test_m16_discover_survives_blip` |
| M17 | `deleteRefList` hides the review and status and resets `refSummaries`. | After a delete, the review panel is hidden. | `::test_m17_delete_list_clears_review` |
| A2 (client) | Save and Save as check `state.filters` and show the server's 409 message. | A name clash shows the message and sends no PUT. | `::test_a2_client_blocks_name_clash` |

**Human check:** the common-flow run in the browser (empty filter, no-match filter, match filter, a reload during a summary, sign-out then sign-in as another code). Screenshots go in the batch gate file.

---

## 7. Batch 6 — bounding hostile input, and dependencies

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| B8 | Abstract scraping moves to the existing `html.parser` extractor (attribute lookup, no regex over the page). Input to any remaining regex is capped at 512 KB. | A 5 MB adversarial page (`<p class="abstract">` × N with no close) is processed in under 1 s. Existing abstract-recovery fixtures still pass. | `tests/test_paper_meta.py::test_b8_adversarial_html_is_linear` (timed, with a generous bound), existing `test_n2_abstract_recovery.py` |
| B9 | `download_pdf_text` passes `max_pages` from config and stops once text exceeds `max_text_chars + TITLE_WINDOW_CHARS`. Extraction runs in a subprocess with a wall-clock timeout from config, plus `RLIMIT_AS` on Linux. | (a) A 500-page fixture reads at most `max_pages`. (b) A PDF that hangs extraction (simulated with a stub worker) is killed at the timeout with a typed error. | `tests/test_fulltext.py::test_b9_max_pages_enforced`, `::test_b9_extraction_timeout` |
| B9 (limit) | **Not code-testable on macOS:** `RLIMIT_AS` is ignored by macOS, so the memory limit is enforced and tested only in CI (Linux). Test `::test_b9_memory_limit_linux` is skipped off Linux, and the skip reason says so. | — | CI-only test, flagged here |
| B10 | Re-pin `requirements-web.txt` to verified versions: urllib3 ≥ 2.6, requests ≥ 2.32.4, certifi current, and a pdfplumber release whose pdfminer.six is ≥ 20251107. pytest and httpx move to `requirements-test.txt`. Add a pip-compile lock with hashes, used by the Dockerfile with `--require-hashes`. `safe_fetch` sends `Accept-Encoding: identity`. | (a) Every pin equals the version installed in the venv. (b) pdfminer.six is at least the patched version. (c) Requests carry `Accept-Encoding: identity`. | `tests/web/test_requirements.py::test_b10_pins_match_installed`, `::test_b10_pdfminer_patched`, `tests/web/test_safe_fetch.py::test_b10_identity_encoding` |
| B10 (advisories) | **Not code-testable offline:** whether any pinned version has an advisory. Human review: run `pip-audit -r requirements-web.txt` at the end of the batch and record its output in the gate file. | — | human step |
| M24 | `monitor.download_pdf` uses `safe_fetch.fetch_pdf`. `NotAPdf` and `TooLarge` count as failed downloads. | An HTML response is not written as `.pdf` and is counted as failed. | `tests/test_monitor.py::test_m24_html_not_saved_as_pdf` |
| M23 | `PDFHandler.download_pdf` writes to `.part` and renames on completion. (This is a GUI-only path, but a cheap fix in shared code.) | An interrupted stream leaves no `.pdf`. | `tests/test_pdf_handler.py::test_m23_partial_download_not_kept` |

---

## 8. Batch 7 — auth hardening

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| M4 | An in-memory per-IP token bucket on `/api/session`, `/lookup` and `/recover` (limits in config; client IP from the first `X-Forwarded-For` hop only when `TRUST_PROXY=1`). A global semaphore on concurrent scrypt returns 503 when full. | The 11th attempt in a minute from one IP gets 429. With the semaphore full, the next attempt gets 503 without running scrypt. | `tests/web/test_auth.py::test_m4_sign_in_rate_limited`, `::test_m4_scrypt_concurrency_bounded` |
| M5 | `reset_pin` also clears the stored LLM key for the merged family and issues a one-time setup token (shown to the owner in the CLI output). The no-PIN sign-in branch requires that token. `/lookup` no longer reveals `pin_set`. | After a reset, the code alone cannot set a PIN. With the token, it can. The stored key is gone. | `tests/web/test_access_codes.py::test_m5_reset_requires_setup_token`, `::test_m5_reset_clears_llm_key`, `::test_m5_lookup_hides_pin_state` |
| M6 | `DELETE /api/session` rotates the account nonce. | A cookie copied before sign-out gets 401 afterwards. | `tests/web/test_auth.py::test_m6_sign_out_invalidates_copies` |
| M7 | `/healthz` returns only `{"status": "ok"}`. The details move to the authenticated `/api/me` (owner-only fields for the owner). | The public `/healthz` has no `db_path`, provider, or key/code flags. | `tests/web/test_app.py::test_m7_healthz_minimal` |
| M8 / M9 | `resolve_client` rejects a user key for a provider with `needs_key: false`, and rejects a user key with an empty provider (400 "say which provider this key is for"). | Both cases return 400, and no request is sent. | `tests/web/test_providers.py::test_m8_key_for_keyless_provider_refused`, `::test_m9_key_without_provider_refused` |
| M3 | `put_llm_key` clears `preferred_model` when the provider changes and no model is given. | Switching provider with a blank model leaves no stale model. | `tests/web/test_llm_key_routes.py::test_m3_provider_change_clears_model` |
| M10 | `current_user` becomes a plain `def`. | It runs in the threadpool (asserted by checking it is not a coroutine function), and all auth tests pass. | `tests/web/test_auth.py::test_m10_current_user_is_sync` |

---

## 9. Batch 8 — tests, CI, docs and cleanup

| ID | Change | Acceptance criterion | Test |
|---|---|---|---|
| T1 | One `_owner_window_start()` helper, used by both `reserve_owner_usage` and `owner_usage_today`. Fix the docstring's `Tests:` path. | Rows at 23h59m block a reservation and at 24h01m do not, and both functions agree at each point. | `tests/web/test_cap.py::test_t1_window_edge_enforce_and_display_agree` |
| T2 | Fixture building the 2026-09-15 schema with raw sqlite3, populated, including two names that differ only in case. | Opening it with `Database()` adds every later column, keeps every row, and reports the case-duplicate names instead of crashing (a documented rename or a logged error, decided when the test is written). | `tests/test_db_migrations.py::test_t2_upgrade_from_2026_09_15_schema`, `::test_t2_case_duplicate_names_handled` |
| M35 | Move the spend-gate assertions (`summarizeChecked`, `reviewChecked`, `summarizeOnePaper`) to the node harness, and delete the matching substring checks. | Declining confirm makes no billed call. A 429 on paper 2 stops the batch. A double click starts one run. | `tests/web/test_frontend_wiring.py::test_m35_confirm_declined_no_call`, `::test_m35_429_stops_batch`, `::test_m35_double_click_single_run` |
| M36 | The contract check compares (method, path) pairs. | A wrong verb in `app.js` fails the test (checked by a mutation in the test itself). | `::test_m36_js_calls_match_method_and_path` |
| M37 / D4 | README and CLAUDE.md say Python 3.12. CI stays at 3.12. | The docs say 3.12. | `tests/web/test_deploy_files.py::test_m37_docs_state_python_312` |
| M38 | `importorskip` becomes `import_module`. | A missing pin fails. | existing test, now failing correctly (verified once by temporarily listing a missing module) |
| M39 | Replace the vacuous `>= 0` assertion with an owner-key-provider test that checks the remaining count decreases by 1. | — | `tests/web/test_summaries_routes.py::test_m39_owner_remaining_decrements` |
| M30 | Turn on `PRAGMA foreign_keys=ON` per connection. Before that, a migration deletes orphaned `user_reviews` and `user_reference_list_items` rows (logging counts). `delete_reference_list` relies on the cascade. | Deleting a list removes its reviews. The migration reports the orphans it removed. | `tests/test_db_migrations.py::test_m30_orphans_cleaned_and_cascade_on` |
| M32 | New `filters.seed.json` (neutral examples, no empty "New Filter" entries) resolved from the repo root and used for new-user seeding. `filters.json` leaves the Docker image and becomes gitignored local state. **Your uncommitted edits to `filters.json` are left as they are.** | New users are seeded from the seed file. The image has no `filters.json`. | `tests/web/test_deploy_files.py::test_m32_image_has_no_personal_filters`, `tests/web/test_accounts.py::test_m32_seed_from_seed_file` |
| M34 | Delete `src/selection.py`, `src/sources/cache.py`, and the unused `db.py` bookmark/search-history methods, and fix `run.sh` no-arg (it prints usage and no longer launches the GUI). GUI-only code that the GUI still imports stays until the GUI is removed, and is listed in the gate file. | No dead module is imported anywhere. | `tests/web/test_deploy_files.py::test_m34_no_dead_modules` |
| LEARNINGS | Add **P13 — a stage narrows the candidate set without saying what it removed** (B1, B2, B3, B4, B5, B7, M20, M22), and **P14 — shared data written from client-supplied fields** (A1). Hand both to `learning-qa` in CAPTURE mode. | Both patterns are in `LEARNINGS.md` with review questions and checklist lines. | human review of the diff |

---

## 10. Adjacent issues found, not fixed

- `gui.py` shares `filtering.py`, `db.py`, `query_builder.py` and the adapters. Batches 1–3 change their interfaces (vocabulary ids, the `search()` return shape, the split filter upsert). The GUI is not updated. Any breakage is recorded in the gate file as a known regression of the retiring app.
- `tests/test_gui_filters.py` and `tests/test_source_picker.py` may need deleting or skipping when the interfaces change. Each is reported, not silently removed.
- The "Bowen AND Marriage" term is quoted as a phrase (user input, not a defect). No change.

## 11. Things this plan cannot prove by test

| Item | Why | Proposed human check |
|---|---|---|
| B1/B2/B3/B7 against live APIs | Unit tests check the requests sent, not how the APIs answer | Before/after live run of the saved filters (Batch 1) |
| B9 memory limit on macOS | macOS ignores `RLIMIT_AS` | CI-only test on Linux |
| B10 advisories | Needs an advisory database | `pip-audit` output in the gate file |
| Front-end flows end to end | The node harness stubs the server | Common-flow browser run per batch, with screenshots |
| M4 behind Railway's proxy | Depends on the header Railway sets | After deploy, confirm the per-IP limit keys on the real client IP |

## 12. Completion

At the end, `docs/spec_coverage_review_fixes.md` lists every ID above as `done` (with test path), `partial` or `not done`, and the review file's findings are marked accordingly.
