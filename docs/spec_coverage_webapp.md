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
| Filter editor FE1–FE3 | `tests/web/test_frontend_wiring.py::test_fe1_*`, `test_fe2_*`, `test_fe3_*` | done |
| `/api/me` effective model | `tests/web/test_llm_key_routes.py::test_me1_*` | done |
| Discover success with a live model | Ollama `qwen3.5:4b` timed out at the client's fixed 120 s; the error path was verified live, the success path only with a stub | partial |
| PDF download for Europe PMC papers | Proxy works (arXiv PDF fetched live); PMC links are web pages → 422. See TODO | partial |
| Summary route fetches guarded, no shared-cache writes, abstract-only labelled | `tests/web/test_summary_fetch_guard.py` (10 tests) | done |
| Legacy filter shape converted on read and run | `tests/web/test_filters_routes.py::test_lf1_*`, `test_lf2_*` | done |
| Discover empty run releases slot | `test_discover_routes.py::test_dt3_no_papers_gives_the_slot_back` | done |
| Pinned connection parameters | `test_safe_fetch.py::test_pinned_get_connects_to_the_ip_and_verifies_the_hostname` | done |
