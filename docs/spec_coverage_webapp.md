# Spec coverage — web app

Spec: the "Make BioRx shareable as a small private web app" brief,
with IDs assigned in `docs/implementation_plan_2026-09-15.md#0`.

Generated 2026-09-16 by checking each named test against the collected
suite — not by hand. Two rows in the original plan named tests that did
not exist; that check is why they were caught rather than claimed.

| ID | Criterion | Verifying test | Status |
|---|---|---|---|
| W1.a | Retrieval modules unmodified | `test_retrieval_modules_unchanged_since_the_web_work_began` | done |
| W1.b | Existing tests stay green | `test_the_guard_would_notice_a_change` | done |
| W2.a | DeepSeek uses the OpenAI dialect | `test_deepseek_uses_openai_dialect_and_bearer_auth` | done |
| W2.b | Anthropic uses the Messages API, model from config | `test_anthropic_uses_messages_api_with_configured_model` | done |
| W2.b | The default model id is a real one | `test_default_anthropic_model_is_a_current_id` | done |
| W2.c | OllamaClient unchanged | `test_ollama_client_interface_unchanged` | done |
| W2.d | user key beats owner key | `test_resolver_prefers_user_key_over_owner_key` | done |
| W2.d | falls back to the owner key | `test_resolver_falls_back_to_owner_key` | done |
| W2.d | typed error when neither exists | `test_resolver_raises_typed_error_when_no_key` | done |
| W3.a | owner key is the default | `test_summary_with_owner_key_only` | done |
| W3.b | a user can store their own key | `test_put_key_then_resolver_uses_it` | done |
| W3.c | never returned in full | `test_key_is_never_returned_in_full` | done |
| W3.c | never logged | `test_key_never_appears_in_logs` | done |
| W3.c | the ciphertext never leaves the server | `test_the_profile_never_carries_the_stored_ciphertext` | done |
| W3.c | .env is gitignored | `test_dotenv_is_gitignored_so_a_filled_in_copy_is_never_committed` | done |
| W3.d | Fernet round-trip | `test_key_roundtrips_through_fernet` | done |
| W3.d | ciphertext is not the plaintext | `test_stored_ciphertext_does_not_contain_plaintext` | done |
| W3.d | no secret is auto-generated (deviation §1.2) | `test_missing_enc_secret_disables_byo_storage_without_generating_one` | done |
| W4.a | a wrong code is refused | `test_wrong_access_code_is_rejected` | done |
| W4.a | the cookie is signed and tamper-evident | `test_cookie_is_signed_and_tamper_evident` | done |
| W4.a | every route requires a session | `test_no_route_can_be_reached_without_a_session` | done |
| W4.b | identity survives a rename | `test_display_name_change_does_not_change_user_id` | done |
| W4.b | a name cannot reach another user's key (deviation §1.3) | `test_typing_another_users_display_name_does_not_reach_their_key` | done |
| W5.a | no build step, no external assets | `test_the_page_references_only_local_assets` | done |
| W5.b | every control's endpoint exists | `test_every_api_path_the_client_calls_exists` | done |
| W5.b | no orphaned controls | `test_the_page_has_no_orphaned_controls` | done |
| W6 | the migration is additive on a populated database | `test_migration_is_additive_on_a_populated_db` | done |
| W6 | the new tables exist | `test_web_app_tables_exist` | done |
| W7.a | the image excludes the desktop GUI | `test_dockerfile_installs_web_requirements_not_pyqt` | done |
| W7.a | railway.json points at the Dockerfile and a healthcheck | `test_railway_config_points_at_the_dockerfile_and_a_healthcheck` | done |
| W7.a | the container starts the app on a volume it can write | `test_the_root_branch_chowns_a_root_owned_volume_then_starts_the_app` | done |
| W7.b | .env.example documents every variable the code reads | `test_env_example_documents_every_variable_the_code_reads` | done |
| D1 | new logic has tests | *(whole suite: 427 tests)* | done |
| D2 | owner key only | `test_summary_with_owner_key_only` | done |
| D2 | user key overrides the owner's | `test_user_key_overrides_owner_key` | done |
| D2 | no key: a clean error, no crash | `test_no_key_returns_clean_error_and_does_not_crash` | done |
| D3 | no secrets in source | `test_env_example_holds_no_real_secret` | done |
| D4 | README covers running and deploying | `test_readme_documents_local_run_and_deploy` | done |

## Not code-testable

| ID | Why | How it is covered instead |
|---|---|---|
| W5.c "match the concepts of the existing gui.py screens" | A judgement about UI equivalence | The client was driven in a real browser against a live server: sign-in, seeded filters, a personal key showing only its last four characters, a live search reaching 47 matching papers, pagination, and Stop. Your sign-off is still the acceptance step. |
| W7.a live deployment | Needs the Railway account, a volume and real env vars | The README's deploy checklist. **The image has never been built** — no Docker daemon here. |
| Live DeepSeek and Anthropic calls | Cost money and need keys | Flagged integration-only (standards L9). Every suite test mocks them. One manual smoke per provider after deploy. |
| D3 "no secrets in source", whole history | The repo test covers the working tree | `git grep` per security.md S1 before the first public push. |

## Per-user settings (docs/implementation_plan_2026-09-17_per_user_settings.md)

| ID | Criterion | Proof | Status |
|---|---|---|---|
| PS1 | `/api/settings/*` gone | `tests/web/test_settings_routes.py::test_ps1_settings_routes_removed` | done |
| PS2 | config files untouched by a PUT attempt | `tests/web/test_settings_routes.py::test_ps2_put_does_not_touch_config_files` | done |
| PS3 | no route exposes config text | `tests/web/test_settings_routes.py::test_ps3_no_route_returns_config_text` | done |
| PS4 | client has no config editor or calls | `tests/web/test_frontend_wiring.py::test_ps4_client_never_touches_server_config` | done |
| PS5 | LLM panel + default sources inside Settings tab | `tests/web/test_frontend_wiring.py::test_ps5_settings_tab_contains_llm_and_sources` | done |
| PS6 | default-sources logic; both pickers use it | `tests/web/test_frontend_wiring.py::test_ps6_default_sources_logic` (node, 9 cases), `test_ps6_both_pickers_use_the_defaults` | done |
| PS7 | storage access guarded | `tests/web/test_frontend_wiring.py::test_ps7_storage_access_is_guarded` | done |
| PS8 | route count 30 → 28 | `tests/web/test_auth.py` `PROTECTED_ROUTE_COUNT = 28` | done |
| PS9 | spec updated | `docs/web_parity_spec_2026-09-17.md` FP3 sections | done |
| PS10 | saved filters open with their fields | `tests/web/test_frontend_wiring.py::test_ps10_client_reads_filters_in_the_shape_the_api_returns` | done |

Browser check (2026-09-17, local server, temp DB): Settings tab layout; saving defaults
updates the Search picker and survives reload; a new filter starts from the defaults;
an existing filter opens with its own sources, keywords and days.

## Review fixes before first push (2026-09-17, commit ef8220f)

| Fix | Proof | Status |
|---|---|---|
| PDF proxy SSRF (every hop, all non-public ranges, fail closed, pinned IP, size cap, PDF magic) | `tests/web/test_safe_fetch.py` (37 tests, incl. a real trickling socket) | done |
| Proxy maps outcomes 403/422/413/502 | `tests/web/test_references_routes.py::test_ref5_pdf_proxy_maps_fetch_outcomes` | done |
| Client-supplied add-item route removed | `test_references_routes.py::test_ref3_no_route_adds_a_client_supplied_paper` | done |
| List delete removes item rows | `test_references_routes.py::test_ref1_delete_list_removes_its_item_rows` | done |
| Duplicate list name → 409, no orphans | `test_ref2_duplicate_list_name_is_409`, `test_ref2_failed_create_with_papers_leaves_nothing`, `test_filter_test_route.py::test_sal3_duplicate_name_is_409` | done |
| CSV bioRxiv version | `test_references_routes.py::test_ref4_csv_uses_the_papers_biorxiv_version` | done |
| Save-as-list: finished only, skips reported | `test_filter_test_route.py::test_sal1_running_search_cannot_be_saved`, `test_sal2_unstorable_papers_are_reported` | done |
| Discover DT1–DT5 | `tests/web/test_discover_routes.py` (21 tests) | done |
| DT6.A result carries the sampled window, and the page reads it | `test_discover_routes.py::test_dt6a_result_reports_the_sampled_window`, `test_frontend_wiring.py::test_dt6a_poll_carries_days_back_into_a_term_filter` | done |
| DT6.B filter from a term uses that window | `test_frontend_wiring.py::test_dt6b_term_filter_uses_discover_window` | done |
| DT6.C shorter open-filter window named | `test_frontend_wiring.py::test_dt6c_shorter_window_is_named_in_the_notice`, `test_dt6c_add_term_puts_the_note_in_its_message` | done |
| DT6.D hint names the window | `test_frontend_wiring.py::test_dt6d_hint_names_the_window` | done |
| DT7.A system prompt from config | `test_discover_routes.py::test_dt7a_system_prompt_comes_from_config`, `test_dt7a_missing_system_prompt_falls_back_with_a_warning` | done |
| DT7.B prompt asks for short verbatim terms | `test_discover_routes.py::test_dt7b_repo_prompt_asks_for_verbatim_short_terms` (instruction present; model compliance is shown per run by DT8) | done |
| DT7.C pure, delimited prompt builder | `test_discover_routes.py::test_dt7c_prompt_builder_is_pure_and_delimited` | done |
| DT8.A–C per-term hit counts | `test_discover_routes.py::test_dt8a_*`, `test_dt8b_*` (4), `test_dt8c_on_topic_phrase_absent_from_papers_counts_zero`, `test_dt8c_short_term_inside_a_longer_word_counts_zero`, `test_dt8a_wildcard_counts_agree_with_the_filter` | done |
| DT8.D–F zero-hit terms flagged, count line, old results | `test_frontend_wiring.py::test_dt8d_zero_hit_chip_is_flagged_not_hidden`, `test_dt8f_old_result_renders_without_counts` | done |
| DT9.A, A2 live check with the filter's query; failure is None | `test_discover_routes.py::test_dt9a_check_uses_the_filters_own_query`, `test_dt9a2_failed_count_is_none_not_zero` (6 cases), `test_dt9a2_a_real_zero_is_zero` | done |
| DT9.B one replacement round, billed as one | `test_dt9b_zero_terms_are_replaced_once`, `test_dt9b_no_replacement_round_when_every_term_finds_papers`, `test_dt9b_two_model_calls_are_billed_as_one`, `test_dt9b_failed_replacement_keeps_first_terms_and_their_cost`, `test_dt9b_replacement_that_never_reached_the_model_keeps_the_cost_counted`, `test_dt9b_replacement_that_cost_tokens_adds_them`, `test_dt9b_replace_prompt_lists_the_failed_terms` | done |
| DT9.C, C2 zero-hit terms not offered, named | `test_dt9c_zero_hit_phrase_is_not_offered`, `test_dt9c2_all_dropped_is_reported`, `test_frontend_wiring.py::test_dt9c2_page_says_when_every_term_was_dropped` | done |
| DT9.D unchecked terms kept, labelled | `test_dt9d_unchecked_terms_are_kept_and_labelled`, `test_frontend_wiring.py::test_dt9e_chip_shows_live_count` | done |
| DT9.E page shows live counts and dropped terms | `test_frontend_wiring.py::test_dt9e_chip_shows_live_count`, `test_dt9e_dropped_terms_are_listed`, `test_dt9e_old_result_unchanged` | done |
| DT9.F phase text, request spacing | `test_dt9f_job_phase_shows_the_check`, `test_dt9f_phase_names_the_check`, `test_dt9f_requests_are_spaced_by_the_configured_delay` | done |
| DT9.G settings in config | `test_dt9g_check_settings_come_from_config`, `test_dt9g_repo_config_has_check_settings` | done |
| DT9-L live check on production | needs a signed-in browser | not done |
| AND1 parser | `tests/test_search_terms.py::test_and1_and_parts` (14 cases) | done |
| AND2 local filter needs every part | `tests/test_filtering.py::test_and2_*` (6) | done |
| AND3 Europe PMC / PubMed query | `tests/test_query_builder.py::test_and3_*` (3, 9 cases); live: `cooperative AND species AND survival` 1,147, `cooperati* AND survival` 6,380 (90 days, 2026-10-07) | done |
| AND4 arXiv query | `tests/test_query_builder.py::test_and4_arxiv_queries`; live: `cooperative AND survival` 62 vs phrase 2 | done |
| AND5 OSF title part, PsyArXiv keywords | `tests/test_query_builder.py::test_and5_*` (3) | done |
| AND6 Discover counts | `tests/web/test_discover_routes.py::test_and6_and_term_counts_need_every_part` | done |
| AND8 page hint | `tests/web/test_frontend_wiring.py::test_and8_and_hint_under_text_boxes` | done |
| AND-L live filter run on production | needs a signed-in browser | not done |
| SW1 within_matches | `tests/test_filtering.py::test_sw1_*` (4) | done |
| SW2 results route narrows and pages | `tests/web/test_searches_routes.py::test_sw2_within_narrows_and_pages`, `test_sw2_no_within_is_unchanged` | done |
| SW3 no source calls | `test_searches_routes.py::test_sw3_within_makes_no_source_calls` | done |
| SW4 save-as-list follows within | `test_searches_routes.py::test_sw4_*` (2) (in this file, not test_filter_test_route.py as planned) | done |
| SW5 summaries list and PDF follow within | `test_searches_routes.py::test_sw5_summaries_and_pdf_follow_within` | done |
| SW6 limits; 2,000 papers < 0.5 s | `test_searches_routes.py::test_sw6_limits_are_enforced`, `test_sw6_real_scale_is_fast` | done |
| SW7 page box, chips, ticks, reset | `tests/web/test_frontend_wiring.py::test_sw7_*` (5) | done |
| SW8 save/PDF send terms; list name | `test_frontend_wiring.py::test_sw8_save_and_pdf_send_the_terms_and_name_them` | done |
| SW local browser check | local server, Europe PMC search `cooperati* AND survival` (16 results) → within `cancer` 10 → `+ lung` 6 → remove `cancer` 6; ticks cleared with notice; Save label "Save all 6 results"; matches `/results?within=` totals | done |
| SW-L live check on production | needs a signed-in browser | not done |
| TA1 both-terms use TITLE_ABS; Title/Abstract boxes unchanged | `tests/test_query_builder.py::test_ta1_both_terms_use_title_abs` (5), `test_ta1_title_and_abstract_boxes_unchanged`; AND3 expectations updated | done |
| TA3 no bare search word | `test_query_builder.py::test_ta3_both_terms_never_bare`; live (first 200 records, 2026-10-07): loneliness 47% → 100% kept, cooperati* AND survival 8% → 100%, "kin selection" 14% → 80% (4 of 5: "kin-selection" with a hyphen fails the local phrase match — TODO) | partial (80% on one term vs the plan's > 90%) |
| TA4 Discover check counts titles and abstracts | `tests/web/test_discover_routes.py::test_ta4_check_counts_titles_and_abstracts`; wording in `test_dt9c_*`, `test_dt9e_*` | done |
| TA5 drift baseline | `tests/web/test_no_retrieval_drift.py` | done |
| TA6 live on production | needs a signed-in browser | not done |
| BW1 window rule | `tests/test_biorxiv_window.py::test_bw1_window` (7 cases), `test_bw1_notes_say_where_the_papers_come_from` | done |
| BW2 long recent range reads the newest days | `tests/test_adapters.py::test_bw2_long_range_reads_only_the_newest_days` | done |
| BW3 old range: no requests, no failure | `tests/test_adapters.py::test_bw3_old_range_makes_no_requests`, `tests/test_orchestrator.py::test_bw3_skip_is_not_a_failure` | done |
| BW4 short range unchanged | `tests/test_adapters.py::test_b7_*` (`test_b7_date_range_used` moved to a recent range: its old 2020–2026-06 range is now a skip) | done |
| BW5 note on the job / monitor log | `tests/test_biorxiv_window.py::test_bw5_notes`, `tests/web/test_searches_routes.py::test_bw5_*` (4) | done |
| BW6 page shows notes | `tests/web/test_frontend_wiring.py::test_bw6_job_notes_shown`, `test_bw6_poll_passes_the_notes` | done |
| BW7 config | `tests/test_biorxiv_window.py::test_bw7_*` (2) | done |
| BW-L live on production | needs a signed-in browser | not done |
| DS1 fetched_status lines | `tests/test_orchestrator.py::test_ds1_fetched_status` | done |
| DS2 overlap named (full and partial) | `tests/test_orchestrator.py::test_ds2_overlap_is_named` | done |
| DS3 records/counts unchanged | full suite | done |
| DS4 drift baseline | `tests/web/test_no_retrieval_drift.py` | done |
| TD1 query syntax quoted | `tests/test_query_builder.py::test_td1_parts_are_read_as_words` (7), `test_td1_injection_stays_inside_one_clause`; live `TITLE_ABS:"COVID-19: outcomes"` 294 hits, accepted | done |
| TD2 unused helper removed | `src/search_terms.py` (no `is_and_term`) | done |
| TD3 arXiv wildcards | `test_query_builder.py::test_td3_*` (2); `tests/test_batch_d.py::test_d_arxiv_query_keeps_wildcards*` (replaced the stripping tests, with live counts) | done |
| TD4 punctuation/hyphens as spaces | `tests/test_filtering.py::test_td4_hyphen_matches_space` (10 cases); live "kin selection" 5/5 kept, "COVID-19: outcomes" 200/200 | done |
| TD5 phrase within one field | `test_filtering.py::test_td5_phrase_does_not_span_title_and_abstract`; Discover `test_td4_td5_discover_counts_follow_the_filter` | done |
| TD6 servers setting read | `tests/test_adapters.py::test_td6_servers_read_from_the_shipped_config_shape`, `test_b7_servers_configurable`, `tests/test_batch_i.py::test_i_biorxiv_medrxiv_retries_on_5xx` (config shape fixed) | done |
| TD7 one date window per search | `tests/web/test_searches_routes.py::test_td7_dates_fixed_once`, `tests/test_adapters.py::test_td7_window_kept_across_pages`, `tests/test_filtering.py::test_td7_fixed_dates_keeps_legacy_and_explicit_ranges` | done |
| TD8 public resolve_active_sources | `tests/test_orchestrator.py::test_td8_old_name_still_works`; BW5 tests use the public name | done |
| TD9 re-send counted as repeat | `test_orchestrator.py::test_td9_resend_after_merge_is_a_repeat` | done |
| TD10 real concurrency test | `test_orchestrator.py::test_ds2_counts_are_not_shared_between_concurrent_searches` (two threads, barrier; mutation-checked) | done |
| TD11 DS table | `docs/implementation_plan_2026-10-07_duplicate_status.md` | done |
| HW1 hyphenated wildcard split | `tests/test_query_builder.py::test_hw1_hyphenated_wildcard_is_split` (5), `test_hw1_arxiv_splits_the_same_way` | done |
| HW2 query is a superset of the filter | `test_query_builder.py::test_hw2_query_is_a_superset_of_the_filter` (2) | done |
| HW3 live | `COVID-1*` 30 days: 0 → 1,229 hits, 195/200 kept; `kin-select*` 0 → 3 (2026-10-07) | done |
| WS1 counts on the job | `tests/web/test_searches_routes.py::test_ws1_limits_on_the_job`, `test_ws1_no_summary_when_nothing_was_cut` | done |
| WS2 orchestrator limit callback | `tests/test_orchestrator.py::test_ws2_limit_callback_carries_counts`, `test_ws2_no_callback_when_read_in_full` | done |
| WS3 summary rows and next step | `tests/test_limit_summary.py::test_ws3_*` (4, incl. the owner's six-source case) | done |
| WS4 advice does not misfire | `tests/test_limit_summary.py::test_ws4_*` (4; mutation-checked) | done |
| WS5 page block | `tests/web/test_frontend_wiring.py::test_ws5_summary_block`, `test_ws5_poll_uses_the_summary_not_the_old_paragraph` | done |
| WS6 wording in config | `tests/test_limit_summary.py::test_ws6_*` (3) | done |
| FL1 filter stores its limit | `tests/web/test_searches_routes.py::test_fl1_*` (6), `tests/web/test_frontend_wiring.py::test_fl1_editor_reads_and_writes_the_limit` | done |
| FL2 Run uses the filter's limit | `test_searches_routes.py::test_fl2_*` (3), `test_frontend_wiring.py::test_fl2_saved_filter_run_sends_no_limit` (mutation-checked) | done |
| FL3 Test uses the filter's limit | `test_searches_routes.py::test_fl3_filter_test_uses_its_limit` | done |
| FL4 monitor | `tests/test_monitor.py::test_fl4_*` (2) | done |
| FL5 limits from config | `test_searches_routes.py::test_fl5_limits_from_config`, `test_frontend_wiring.py::test_fl5_limits_come_from_config_not_the_page` | done |
| FL6 limit beside Run | `test_frontend_wiring.py::test_fl6_run_limit_line` | done |
| DW1 window on the job | `tests/web/test_searches_routes.py::test_dw1_window_on_the_job` (mutation-checked) | done |
| DW2 window text | `tests/web/test_frontend_wiring.py::test_dw2_date_window_text` (mutation-checked) | done |
| DW3 lines carry the window | `test_frontend_wiring.py::test_dw3_lines_carry_the_window` (mutation-checked), `test_dw3_render_results_and_filter_test_use_the_helpers`, `test_dw3_poll_keeps_the_window_for_the_results` | done |
| DW4 hint in config | `tests/test_search_limits.py::test_dw4_*` (5), `test_searches_routes.py::test_dw4_hint_in_config_endpoint`, `test_frontend_wiring.py::test_dw4_hint_read_from_config` | done |
| DW5 monitor logs the window | `tests/test_monitor.py::test_dw5_window_logged` (mutation-checked) | done |
| AY1 All years dates | `tests/test_filtering.py::test_ay1_all_years_dates` | done |
| AY2 only true widens | `test_filtering.py::test_ay2_only_true_widens` (7; mutation-checked) | done |
| AY3 start date from config | `tests/test_search_limits.py::test_ay3_*` (7) | done |
| AY4 saved filters and editor | `test_searches_routes.py::test_ay4_*` (6; mutation-checked), `test_frontend_wiring.py::test_ay4_date_fields_shown`, `test_ay4_editor_reads_and_writes_all_years` | done |
| AY5 Search tab sends all_years | `test_frontend_wiring.py::test_ay5_manual_filter_all_years` (mutation-checked) | done |
| AY6 every source gets the window | `test_searches_routes.py::test_ay6_all_sources_get_the_window` (real orchestrator; mutation-checked) | done |
| AY7 live | local server 2026-10-08, `internal family systems`, All years, Europe PMC + PubMed: "Results — 27 matching · Searched all years (to 2026-10-08)" (Europe PMC's 28 less one preprint merged with its published version); production needs the owner signed in | done (local) |
| KW1 keywords in the record dict | `tests/test_adapters.py::test_kw1_keywords_in_the_dict` (mutation-checked) | done |
| KW2 every keyword kept | `test_adapters.py::test_kw2_all_keywords_kept` (25 keywords; mutation-checked for both adapters) | done |
| KW3 keywords merged | `tests/test_dedup.py::test_kw3_keywords_merged` (both orders; mutation-checked) | done |
| KW4 query searches KW | `tests/test_query_builder.py::test_kw4_title_abs_parts_also_search_keywords`; pinned clause tests updated | done |
| KW5 filter and search-within match keywords | `tests/test_filtering.py::test_kw5_*` (2; mutation-checked) | done |
| KW6 adversarial | `test_filtering.py::test_kw6_*` (2; mutation-checked: keywords joined into one field fails) | done |
| KW7 query superset of the filter | `test_query_builder.py::test_kw7_query_is_a_superset_of_the_filter` (6; mutation-checked) | done |
| KW8 page | `tests/web/test_frontend_wiring.py::test_kw8_*` (2; mutation-checked) | done |
| KW9 route | `tests/web/test_searches_routes.py::test_kw9_keyword_only_paper_kept` | done |
| KW10 live | real orchestrator and local browser 2026-10-08: 30 papers, the 3 keyword-only PubMed papers kept, Keywords line shown | done (local) |
| TG1 numeric settings read defensively | `tests/test_search_limits.py::test_tg1_*` (10), `tests/web/test_searches_routes.py::test_tg1_search_starts_with_a_broken_config` (mutation-checked) | done |
| TG2 OSF default shared; bioRxiv window settings | `test_search_limits.py::test_tg2_osf_terms_default_shared` (4), `tests/test_biorxiv_window.py::test_tg2_bad_window_settings_fall_back` (5) (mutation-checked) | done |
| TG3 All years names its start | `tests/web/test_frontend_wiring.py::test_dw2_date_window_text` (mutation-checked) | done |
| TG4 fallbacks match the config | `test_search_limits.py::test_tg4_fallbacks_match_the_shipped_config` (mutation-checked) | done |
| TG5 query model is whole-word | `tests/test_query_builder.py::test_tg5_query_model_is_whole_word`, `test_kw7_*` (+`ketamine`) (mutation-checked) | done |
| Filter editor FE1–FE3 | `tests/web/test_frontend_wiring.py::test_fe1_*`, `test_fe2_*`, `test_fe3_*` | done |
| `/api/me` effective model | `tests/web/test_llm_key_routes.py::test_me1_*` | done |
| Discover success with a live model | Ollama `qwen3.5:4b` timed out at the client's fixed 120 s; the error path was verified live, the success path only with a stub | partial |
| PDF download for Europe PMC papers | Proxy works (arXiv PDF fetched live); PMC links are web pages → 422. See TODO | partial |
| Summary route fetches guarded, no shared-cache writes, abstract-only labelled | `tests/web/test_summary_fetch_guard.py` (10 tests) | done |
| Legacy filter shape converted on read and run | `tests/web/test_filters_routes.py::test_lf1_*`, `test_lf2_*` | done |
| Discover empty run releases slot | `test_discover_routes.py::test_dt3_no_papers_gives_the_slot_back` | done |
| Pinned connection parameters | `test_safe_fetch.py::test_pinned_get_connects_to_the_ip_and_verifies_the_hostname` | done |
