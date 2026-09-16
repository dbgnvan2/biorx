# Implementation plan — Making biorx GREAT

**Spec:** `docs/spec_make_it_great.md` (APPROVED 2026-09-16, revision 5)
**Status:** DRAFT — awaiting Dave's approval. No implementation code written.
**Runs after:** the amended backlog, `docs/implementation_plan_2026-09-16_backlog.md` §6
(order H → A → D → C → E → F reduced → I).

---

## 0. How this plan works

- **IDs** are the spec's, verbatim: `G0.1`, `GL.1` … `GL.9`, `G1.1` … `G6.1`,
  decisions `D1`–`D7`, `O1`–`O4`. Plan-only decisions are `PD1`… (§3).
- **Every acceptance criterion** in the spec appears in §2 with the test that
  proves it. Test names contain the spec ID (`test_gl_2_…`). Tests listed in the
  spec are used verbatim; tests this plan adds are marked *(plan)*.
- **Chunks.** Each chunk is one or a few commits, tests first, then a QA gate
  recorded in `docs/cycles/2026-MM-DD_<chunk>-qa-gate.md` as for N1/N2, then
  `/chdp` or `/csdp`. Every new test is mutation-checked with the verbatim
  original line (learnings P27).
- **Traceability.** Every function satisfying a criterion carries
  `Purpose / Spec / Tests` in its docstring. `docs/spec_coverage.md` gains a
  section per chunk.
- **Retrieval lock.** Chunks marked **[W1.a]** re-baseline
  `tests/web/test_no_retrieval_drift.py` in the same commit and name the
  protected file changed (spec §4).
- **Integration-only** criteria (live HTTP, LLM, OS schedulers, OS keychains,
  installers) are labelled **INT** and are not counted as covered by mocks.
  **HUMAN** marks criteria that need a person (§4).

---

## 1. Chunk order and dependencies

| # | Chunk | Spec items | Depends on | Size |
|---|---|---|---|---|
| 1 | Search precision | G0.1 [W1.a] | backlog D, C | S |
| 2 | Local server | GL.1, GL.2 | backlog H | M |
| 3 | Single user + keys | GL.3, GL.4, O3 | 2 | M |
| 4 | Setup screen + guides | GL.8, GL.9, O4 | 3 | M |
| 5a | Parity: paper detail + PDF download | GL.5 | 3 | M |
| 5b | Parity: reference lists + Excel export | GL.5 | 5a | M |
| 5c | Parity: filter editor + `filters.json` import | GL.5 | 3 | M |
| 5d | Retire `gui.py` | GL.5 | 5a–5c | S |
| 6 | Install + CI install jobs | GL.6, GL.7 | 2, 4 | M |
| 7 | CLI consolidation | O2, PD1 | 3, 4 | M |
| 8 | Identity | G1.1, G1.2 [W1.a], G1.3 [W1.a] | 1; OpenAlex terms verified (V1) for G1.3 step 3 | L |
| 9 | New-since-last-run | G3.1 | 8 | M |
| 10 | Scheduling | G3.2, D6 | 7, 9 | L |
| 11 | Backups | G6.1 | 3 | M |
| 12 | Ranking | G5.1 [W1.a] | 8; V1 | M |
| 13 | Value tail | G3.3, G3.4, G4.1 [W1.a], G5.2, G5.3 | 4, 5b, 8; V2 for G4.1; V3 for G5.3 | L, split when reached |

Chunks 5a–5c are independent of each other after chunk 3 and may be reordered.
Chunk 11 may move earlier if real data accumulates before chunk 10.

**Verifications before the dependent chunk starts** (spec §0 "to verify"):

| ID | What | Needed by | How |
|---|---|---|---|
| V1 | OpenAlex access terms (key? daily allowance?) | 8 (G1.3 step 3), 12, 13 (G5.2) | Read current OpenAlex docs; record in the chunk's gate file |
| V2 | Which preprint servers are still on OSF | 13 (G4.1) | `GET https://api.osf.io/v2/preprint_providers/`; save the response as a fixture |
| V3 | SQLite FTS5 on the Windows Python build | 13 (G5.3) | Added to backlog batch H's CI run |
| V4 | `claude-haiku-4-5` alias vs `claude-haiku-4-5-20251001`; DeepSeek thinking-mode switch | 4 | Provider docs + one live call with Dave's key (INT) |

---

## 2. Acceptance criteria → tests

### Chunk 1 — G0.1 Europe PMC "both" field [W1.a]

| Criterion | Test | Files |
|---|---|---|
| G0.1 "both" terms sent as `TITLE_ABS:` by default | `tests/test_query_builder.py::test_g0_1_both_field_uses_title_abs_by_default` | `src/sources/query_builder.py` |
| G0.1 `search_full_text` option sends bare terms | `tests/test_query_builder.py::test_g0_1_full_text_option_sends_bare_terms` | same |
| G0.1 option reaches the query builder from the web UI | *(plan)* `tests/web/test_frontend_wiring.py::test_g0_1_full_text_checkbox_reaches_filter_dict` (learnings P25) | `web/static/app.js`, `web/routes_searches.py` |
| G0.1 PubMed path checked for the same semantics | *(plan)* `tests/test_query_builder.py::test_g0_1_pubmed_query_uses_same_field_rule` | `src/sources/pubmed.py` |
| G0.1 misleading comment fixed | covered by review; no test | `query_builder.py:55` |
| G0.1 changelog + README announce fewer hits | *(plan)* new `tests/test_docs.py::test_g0_1_changelog_mentions_title_abs_default` (parsed changelog entry, not substring) | `CHANGELOG.md`, `README.md` |
| G0.1 live hit-count comparison | **INT** — recorded in gate file | — |

### Chunk 2 — GL.1 launcher, GL.2 local-server security

| Criterion | Test | Files |
|---|---|---|
| GL.1 existing `~/preprints/` kept | `tests/test_launcher.py::test_gl_1_existing_preprints_dir_is_kept` | new `src/local_app.py` (launcher), `src/db.py`, `src/pdf_handler.py` |
| GL.1 OS default when absent (macOS and Windows branches) | `tests/test_launcher.py::test_gl_1_platform_default_when_absent` | same; adds `platformdirs` |
| GL.1 `BIORX_DB_PATH` / `DATA_DIR` still override | *(plan)* `test_gl_1_env_override_wins` | same |
| GL.1 port in use → another port, reported | `test_gl_1_port_in_use_picks_another_and_reports_it` | same |
| GL.1 startup prints URL, port, data dir, DB path | *(plan)* `test_gl_1_startup_banner_lists_effective_paths` (learnings P16) | same |
| GL.1 second launch reuses the running instance | `test_gl_1_second_launch_reuses_running_instance` (lock file + health probe) | same |
| GL.1 no hardcoded `/` or `~` path strings in new code | *(plan)* `test_gl_1_no_string_path_joins` (AST scan of `src/local_app.py`) | same |
| GL.2 binds loopback only | `tests/web/test_local_security.py::test_gl_2_server_binds_loopback_only` | `src/local_app.py` |
| GL.2 foreign `Host` header refused | `test_gl_2_foreign_host_header_is_refused` | new middleware in `web/app.py` |
| GL.2 every `/api` route requires the launch token (exact route count) | `test_gl_2_api_without_token_is_401` | `web/auth.py` (rewritten) |
| GL.2 token → `SameSite=Strict`, `HttpOnly` cookie | *(plan)* `test_gl_2_token_exchange_sets_strict_httponly_cookie` | same |
| GL.2 token changes per launch | *(plan)* `test_gl_2_token_differs_between_launches` | same |
| GL.2 no CORS headers | *(plan)* `test_gl_2_no_cors_headers_on_api` | `web/app.py` |
| GL.2 browser opens and works | **INT** — launch on macOS and Windows runners (chunk 6) | — |

Tests that start the real launcher shim the browser opener at session scope in
`conftest.py` and use a temp data dir through the env override, never `cwd`
(learnings P34).

### Chunk 3 — GL.3 remove multi-user/cloud, GL.4 keys in OS store, O3

| Criterion | Test | Files |
|---|---|---|
| GL.3 deploy files removed | *(plan)* `tests/test_gl_3_removed.py::test_gl_3_deploy_files_absent` (exact list) | delete `Dockerfile`, `railway.json`, `docker-entrypoint.sh`, `tests/web/test_deploy_files.py` |
| GL.3 no access-code setting remains | `test_gl_3_no_access_code_setting_remains` (parsed settings/env names) | `web/deps.py`, `web/auth.py`, `.env.example` |
| GL.3 removed routes return 404 | `test_gl_3_removed_routes_are_404` (`/api/session` POST, display-name PATCH) | `web/routes_session.py` |
| GL.3 spend cap removed incl. `summary_daily_cap_per_user` | *(plan)* `test_gl_3_no_daily_cap_is_enforced` (26 summaries with a patched provider all run) | `web/routes_summaries.py`, `llm_config.yaml` |
| GL.3 earlier web user rows reassigned to `LOCAL_USER_ID` | `test_gl_3_existing_user_rows_are_reassigned` (populated DB, exact before/after counts) | `src/db.py`, `src/user_store.py` |
| GL.3 README deploy section removed | *(plan)* `tests/test_docs.py::test_gl_3_readme_has_no_deploy_section` (parsed headings) | `README.md` |
| O3 LLM calls recorded as history, no cap | *(plan)* `test_o3_each_llm_call_is_recorded_with_provider_and_model` | `usage_events` reused |
| GL.4 key saved to keyring, not DB | `tests/web/test_keys.py::test_gl_4_key_is_saved_to_keyring_not_db` (in-memory keyring backend) | new `src/key_store.py`; adds `keyring` |
| GL.4 env var overrides keyring | `test_gl_4_env_var_overrides_keyring` | same |
| GL.4 only last 4 chars stored for display | *(plan)* `test_gl_4_db_holds_no_more_than_last4` (scan every text/blob column for the key) | same |
| GL.4 DB key migrates, column cleared | `test_gl_4_db_key_migrates_and_column_is_cleared` | `src/db.py` |
| GL.4 undecryptable DB key cleared, user told to re-enter | *(plan)* `test_gl_4_undecryptable_key_is_cleared_with_message` | same |
| GL.4 no keyring backend → env only, no plaintext | `test_gl_4_no_backend_does_not_write_plaintext` (fingerprint data dir before/after) | same |
| GL.4 `src/crypto.py` and its tests removed | *(plan)* `test_gl_4_crypto_module_absent` | delete `src/crypto.py`, `tests/web/test_crypto.py` |
| GL.4 real Keychain / Credential Manager round trip | **INT** — CI job on macOS and Windows runners | — |

**External action for Dave (PD2):** shut down the Railway service after
exporting anything on its volume that should be kept. The code removal does not
touch the running deployment.

### Chunk 4 — GL.8 setup screen, GL.9 guides, O4

| Criterion | Test | Files |
|---|---|---|
| GL.8 default provider is not Ollama | `tests/web/test_llm_config.py::test_gl_8_default_provider_is_not_ollama` | `llm_config.yaml` |
| GL.8 no provider → summary controls disabled with reason | `tests/web/test_setup.py::test_gl_8_no_provider_disables_summaries_with_reason` (rendered state from `/api/settings`) | `web/static/app.js`, new `web/routes_settings.py` |
| GL.8 search, download, export work with no provider | `test_gl_8_search_download_export_work_with_no_provider` | — |
| GL.8 "Test key" maps provider errors to plain messages | `test_gl_8_test_key_reports_invalid_key`; *(plan)* `test_gl_8_test_key_reports_no_credit`, `test_gl_8_test_key_reports_network_failure`, `test_gl_8_test_key_reports_success` | `src/llm_providers.py` |
| GL.8 Ollama test: running / not running / model missing | *(plan)* `test_gl_8_ollama_test_three_outcomes` | same |
| GL.8 test call is minimal and time-boxed | *(plan)* `test_gl_8_test_call_uses_max_tokens_1_and_timeout` | same |
| GL.8 settings control values reach the backend | *(plan)* `tests/web/test_frontend_wiring.py::test_gl_8_provider_model_and_key_fields_reach_routes` (P25) | `app.js` |
| O4 default models: `deepseek-flash`, Anthropic Haiku 4.5; stronger selectable | *(plan)* `test_o4_default_models_from_config`; V4 settles alias vs dated id | `llm_config.yaml` |
| O4 DeepSeek thinking mode off for summaries | *(plan)* `test_o4_deepseek_summary_request_disables_thinking` (captured payload) | `src/llm_providers.py` |
| GL.8 live key test | **INT** | — |
| GL.9 settings links to both guides, served locally | `test_gl_9_settings_links_to_both_guides` | `web/app.py` serves `docs/guides/` rendered |
| GL.9 guide messages match the app, both directions | `test_gl_9_guide_messages_match_the_app` | new message table in `web/routes_settings.py` |
| GL.9 guide model ids match `llm_config.yaml` | `test_gl_9_guide_model_ids_match_llm_config` (parsed code blocks) | `docs/guides/installing_ollama.md` |
| GL.9 README links both guides | *(plan)* `tests/test_docs.py::test_gl_9_readme_links_guides` | `README.md` |
| GL.9 guides followed on fresh account/machine, both OS | **HUMAN** (§4) | — |

The two guides were written on 2026-09-16 (commit with this plan). The Ollama
guide recommends `qwen3.5:4b` / `qwen3.5:9b`; `llm_config.yaml` currently says
`qwen:7b`, so `test_gl_9_guide_model_ids_match_llm_config` starts red and the
config change in this chunk turns it green.

### Chunks 5a–5d — GL.5 parity, then retire `gui.py`

The parity table below is the GL.5 checklist. Each row's test drives the web UI's
control through to the backend (P25), not only the route.

| Row | Desktop feature (source) | Test | Chunk |
|---|---|---|---|
| GL.5.1 | Paper detail: abstract tab (`gui.py:385`) | `test_gl_5_1_paper_detail_shows_abstract` | 5a |
| GL.5.2 | Discussion tab (`gui.py:412`) | `test_gl_5_2_paper_detail_shows_discussion` | 5a |
| GL.5.3 | Open PDF / open paper page (`gui.py:401,418`) | `test_gl_5_3_open_links_use_pdf_and_source_urls` | 5a |
| GL.5.4 | Load from local PDF (`gui.py:403`) | `test_gl_5_4_local_pdf_upload_extracts_text` | 5a |
| GL.5.5 | Download selected / all PDFs, with stop (`gui.py:1667–1674`) | `test_gl_5_5_download_job_reports_n_of_m_and_stops` | 5a |
| GL.5.6 | Download failures counted, not silent (learnings P2) | `test_gl_5_6_failed_downloads_are_listed` | 5a |
| GL.5.7 | Save selection as reference list (`gui.py:721`) | `test_gl_5_7_save_selection_as_list` | 5b |
| GL.5.8 | View / remove from / delete list (`gui.py:1626,1660`) | `test_gl_5_8_list_view_remove_delete` | 5b |
| GL.5.9 | Excel export (`gui.py:1671`) | `test_gl_5_9_excel_export_row_count_matches_list` | 5b |
| GL.5.10 | DOI-less paper added twice is one item | `test_gl_5_doi_less_paper_added_twice_is_one_item` | 5b |
| GL.5.11 | Filter editor: OR groups (`gui.py:1142`) | `test_gl_5_11_or_groups_round_trip` | 5c |
| GL.5.12 | Test filter (`gui.py:1316`) | `test_gl_5_12_test_filter_runs_without_saving` | 5c |
| GL.5.13 | Save as (`gui.py:1314`) | `test_gl_5_13_save_as_creates_copy` | 5c |
| GL.5.14 | Run selected / run all enabled (`gui.py:650–652`) | `test_gl_5_14_run_selected_and_run_all_enabled` | 5c |
| GL.5.15 | Pagination and select all/none (`gui.py:718,731`) | `test_gl_5_15_pagination_and_selection` | 5c |
| GL.5.16 | `filters.json` imported once, "N of M" reported, duplicates renamed | `test_gl_5_filters_json_imported_once` (dirty state: second launch imports 0) | 5c |
| GL.5.17 | `reference_list_items` keyed on `canonical_id` (migration, exact counts) | *(plan)* `test_gl_5_17_reference_items_migration_preserves_rows` | 5b |

Before 5a starts, a full read of `gui.py` confirms this list is complete
(learnings P3); any missing feature is added as a row, not skipped.

**Chunk 5d (retire):** delete `gui.py`, `run_gui.sh`, PyQt from
`requirements.txt`, GUI tests; `tests/test_gl_5_retired.py::test_gl_5_gui_removed_and_no_pyqt_import`
(AST scan for `PyQt6` imports). Update the project `CLAUDE.md` and `README.md`,
which still describe a PyQt + Ollama app (adjacent issue, §5). The desktop
`AbstractFetchWorker` logic already lives in `src/paper_meta.py` (N2), so nothing
of it is lost.

### Chunk 6 — GL.6 install, GL.7 CI

| Criterion | Test | Files |
|---|---|---|
| GL.6 `install.sh` (macOS) and `install.ps1` (Windows) create a pinned 3.12 environment and launcher | **INT** — CI job per OS on a clean runner: run installer, start app, `GET /healthz` = 200 | new `install.sh`, `install.ps1` |
| GL.6 installer does not install or mention Ollama as a step | *(plan)* `tests/test_install_scripts.py::test_gl_6_installers_do_not_reference_ollama` (tokenised, comments excluded) | same |
| GL.6 Ollama checks removed from `run.sh` | *(plan)* `test_gl_6_run_sh_has_no_ollama_check` — or `run.sh` deleted in favour of the launcher (PD3) | `run.sh` |
| GL.6 macOS `.command` / Windows shortcut created | **INT** (same CI job asserts the file exists) | — |
| GL.6 install instructions tested by hand on both OS | **HUMAN** | `README.md` |
| GL.7 suite runs on `macos-latest` and `windows-latest` | the workflow; every assertion lives in the suite | `.github/workflows/tests.yml` (from backlog H) |

### Chunk 7 — CLI consolidation (O2, PD1)

| Criterion | Test | Files |
|---|---|---|
| O2 `monitor.py` folded into `biorx run-due` | *(plan)* `tests/test_cli.py::test_o2_run_due_replaces_monitor` | new `src/cli.py`; delete `agents/monitor.py` |
| O2 exit code 2 when sources failed (carried from backlog D) | *(plan)* `test_o2_run_due_exit_code_reflects_failed_sources` | same |
| PD1 `search_agent.py` → `biorx search` | *(plan)* `test_pd1_search_command_uses_orchestrator` | delete `agents/search_agent.py` |
| PD1 `summarization_agent.py` → `biorx summarize` via the provider layer | *(plan)* `test_pd1_summarize_uses_llm_providers_not_legacy_llm` | delete `agents/summarization_agent.py`, `src/llm.py` legacy client |
| PD1 legacy `src/llm.py` gone; nothing imports it | *(plan)* `test_pd1_no_import_of_legacy_llm` (AST) | — |

### Chunk 8 — G1 identity

| Criterion | Test | Files |
|---|---|---|
| G1.1 `canonical_id` unchanged after enrichment | `tests/test_identity.py::test_g1_1_canonical_id_unchanged_after_enrichment` | `src/db.py` |
| G1.1 lookup by arXiv id finds DOI-keyed paper | `test_g1_1_lookup_by_arxiv_id_finds_doi_keyed_paper` | `src/db.py` |
| G1.1 new columns on a populated DB, rows preserved | `test_g1_1_migration_on_populated_db_preserves_rows` (dirty state, exact counts) | `src/db.py` |
| G1.1 failed external call leaves the row untouched | *(plan)* `test_g1_1_failed_lookup_changes_nothing` | — |
| G1.2 arXiv prefix/version normalised and deduped | `tests/test_dedup.py::test_g1_2_arxiv_prefix_and_version_dedup` | `src/sources/dedup.py`, `schema.py` [W1.a] |
| G1.2 NFKC title match | `test_g1_2_nfkc_title_match` | same |
| G1.2 correction notice does not merge with article (adversarial) | `test_g1_2_correction_notice_does_not_merge_with_article` | same |
| G1.3 bioRxiv `published` DOI creates link | `tests/test_paper_links.py::test_g1_3_biorxiv_published_doi_creates_link` (recorded real response) | `src/sources/biorxiv_medrxiv.py` [W1.a], new `src/paper_links.py` |
| G1.3 Crossref `is-preprint-of` creates link | *(plan)* `test_g1_3_crossref_relation_creates_link` (recorded real response) | `src/sources/crossref.py` [W1.a] |
| G1.3 linked versions remain two rows | `test_g1_3_linked_versions_remain_two_rows` | — |
| G1.3 lookup failure writes no link, stays retryable | `test_g1_3_lookup_failure_writes_no_link` | — |
| G1.3 UI shows "Published as" / "Preprint version" | *(plan)* `test_g1_3_detail_view_shows_link_both_directions` | `app.js` |
| G1.3 OpenAlex fallback (step 3) | only after V1; **INT** for live | — |

### Chunk 9 — G3.1 new-since-last-run

| Criterion | Test | Files |
|---|---|---|
| G3.1 failed source does not inflate the new count | `tests/test_deltas.py::test_g3_1_failed_source_does_not_inflate_new_count` | new `src/deltas.py`, `filter_runs` table |
| G3.1 incomplete run: compared to last complete run, and says so | *(plan)* `test_g3_1_incomplete_run_uses_last_complete_baseline_and_labels_it` | same |
| G3.1 paper missing from an incomplete run is not "removed" | *(plan)* `test_g3_1_missing_from_incomplete_run_is_not_removed` | same |
| G3.1 id-scheme change → "baseline reset" | `test_g3_1_first_run_after_id_change_reports_reset` | same |
| G3.1 second run reports only additions | `test_g3_1_second_run_reports_only_additions` | same |
| G3.1 newly published version → "now published" | *(plan)* `test_g3_1_published_version_reported_as_now_published` | uses `paper_links` |

### Chunk 10 — G3.2 scheduling, D6

| Criterion | Test | Files |
|---|---|---|
| G3.2 due schedule runs once on launch | `tests/test_schedules.py::test_g3_2_due_schedule_runs_once_on_launch` | new `src/schedules.py`, `filter_schedules` table |
| G3.2 app and OS task never run the same schedule twice | `test_g3_2_concurrent_triggers_run_once` (two threads, real SQLite) | same |
| G3.2 / D6 scheduled run calls no LLM | `test_g3_2_scheduled_run_calls_no_llm` (provider layer patched to raise) | same |
| G3.2 OS entry install/remove command per OS | `test_g3_2_os_entry_install_and_remove` (launchd plist / `schtasks` args, patched) | new `src/os_scheduler.py` |
| G3.2 schedule survives restart | *(plan)* `test_g3_2_schedule_persists_across_app_restart` | — |
| G3.2 feed shows results; optional OS notification | *(plan)* `test_g3_2_feed_lists_new_papers_per_filter` | `app.js`, new route |
| G3.2 real launchd / Task Scheduler registration | **INT** + **HUMAN** check on each OS | — |

### Chunk 11 — G6.1 backups

| Criterion | Test | Files |
|---|---|---|
| G6.1 backup during writes is consistent | `tests/test_backups.py::test_g6_1_backup_during_writes_is_consistent` (`PRAGMA integrity_check`, exact row counts) | new `src/backups.py` |
| G6.1 backup contains no API key | `test_g6_1_backup_contains_no_api_key` | — |
| G6.1 restore round trip | `test_g6_1_restore_round_trip` | — |
| G6.1 retention keeps N | `test_g6_1_retention_keeps_n` | — |
| G6.1 due-on-launch after configured days | *(plan)* `test_g6_1_backup_runs_when_older_than_configured_days` | — |
| G6.1 failure shown in UI | *(plan)* `test_g6_1_failed_backup_surfaces_in_status_route` | — |
| G6.1 restore drill on a real machine | **HUMAN** | — |

### Chunk 12 — G5.1 ranking [W1.a]

| Criterion | Test | Files |
|---|---|---|
| G5.1 old, highly cited, irrelevant ranks below recent relevant (adversarial) | `tests/test_ranking.py::test_g5_1_old_highly_cited_irrelevant_paper_ranks_below_recent_relevant` | `src/sources/orchestrator.py` |
| G5.1 failed lookup is neutral, not zero | `test_g5_1_failed_lookup_is_neutral_not_zero` | same |
| G5.1 retracted ranks last regardless of citations | `test_g5_1_retracted_paper_ranks_last_regardless_of_citations` | same |
| G5.1 weights from config | `test_g5_1_weights_come_from_config` | `sources_config.yaml` |
| G5.1 source-order relevance input normalised per source | *(plan)* `test_g5_1_source_position_normalised_per_source` | same |
| G5.1 more citations never lowers score, all else equal (monotonic, learnings P7) | *(plan)* `test_g5_1_score_monotonic_in_citations` | same |
| G5.1 lookups batched, cached, time-boxed; ranking proceeds on failure | *(plan)* `test_g5_1_lookup_timeout_still_returns_ranked_results` | `src/sources/cache.py` |

### Chunk 13 — value tail (split into its own chunks when reached)

| Criterion | Test |
|---|---|
| G3.3 paid batch needs confirmation showing N, provider, model | `test_g3_3_batch_requires_confirmation_for_paid_provider` |
| G3.3 one failure does not stop the batch; reported | `test_g3_3_one_failure_does_not_stop_the_batch_and_is_reported` |
| G3.4 BibTeX/RIS/CSL-JSON round trip, count = list size | *(plan)* `test_g3_4_each_format_round_trips_with_exact_count` |
| G3.4 DOI-less paper exports | `test_g3_4_doi_less_paper_exports` |
| G3.4 "exported N of M" when any entry cannot be written | *(plan)* `test_g3_4_partial_export_is_reported` |
| G4.1 existing PsyArXiv/SocArXiv adapter tests pass against `osf.py` | existing `tests/test_adapters.py` cases, unchanged |
| G4.1 saved filter naming `psyarxiv` still runs | `test_g4_1_saved_filter_with_psyarxiv_still_runs` |
| G4.1 per-server "fetched N pages, kept M of K" logged; cap announced | *(plan)* `test_g4_1_page_cap_is_announced_per_server` |
| G5.2 lookup failure distinct from empty | `test_g5_2_lookup_failure_is_distinct_from_empty` |
| G5.3 extracted text persisted | `test_g5_3_extracted_text_is_persisted` |
| G5.3 FTS finds a term only in full text | `test_g5_3_fts_finds_term_only_in_full_text` |
| G5.3 expansion terms shown before search | `test_g5_3_expansion_terms_shown_before_search` |
| G5.3 expansion failure falls back and says so | `test_g5_3_expansion_failure_falls_back_to_plain_query` |
| G5.3 expansion sends only the query | `test_g5_3_expansion_sends_only_the_query` |
| G5.3 expansion quality | **INT** |
| G5.3 layer 3 (embeddings) | not planned — spec says build only if layers 1–2 prove insufficient |

---

## 3. Plan-level decisions (need approval with this plan)

| # | Question | Recommendation |
|---|---|---|
| PD1 | O2 left `search_agent.py` and `summarization_agent.py` to the plan | Fold both into a single `biorx` command (`serve`, `search`, `summarize`, `run-due`) that uses the same orchestrator and provider layer as the web UI; delete the agents and the legacy Ollama-only `src/llm.py`. This supersedes backlog batch B. |
| PD2 | The running Railway service | Dave exports anything on the Railway volume worth keeping, then shuts the service down. Not done by code. |
| PD3 | `run.sh` / `run_gui.sh` | Delete both in favour of the installer's launcher and `biorx serve`. |
| PD4 | How `install.sh` / `install.ps1` get Python 3.12 | Use `uv`, which installs its own Python, so users need nothing preinstalled. The scripts download `uv` from its official installer; the script prints what it will download and asks before doing so. |
| PD5 | Guides in the app | Render the two Markdown guides to HTML at build time and serve them from the local app, so they work offline and match the installed version. |

---

## 4. Human review (cannot be made code-testable)

| Item | Who | When | Record |
|---|---|---|---|
| GL.9 follow the API-key guide on a fresh DeepSeek and a fresh Anthropic account; fix button names | Dave or a colleague | before first release to colleagues | gate file for chunk 4 |
| GL.9 follow the Ollama guide on an 8 GB machine; confirm `qwen3.5:4b` summary quality and speed are acceptable | same | same | same |
| GL.6 install from scratch on a Mac and a Windows PC | same | chunk 6 | gate file |
| G3.2 OS scheduler entry fires while the app is closed, both OS | same | chunk 10 | gate file |
| G6.1 restore from a real backup | same | chunk 11 | gate file |
| G0.1 confirm narrower default returns the papers Dave expects on two real saved filters | Dave | chunk 1 | gate file |

---

## 5. Adjacent issues found, not fixed by this plan

| Issue | Where handled |
|---|---|
| Project `CLAUDE.md` describes a PyQt + Ollama + Qwen app | chunk 5d |
| `README.md` "Local LLM: Qwen 7B via Ollama" and deploy sections | chunks 3 and 5d |
| `llm_config.yaml` `deepseek-chat` is no longer in DeepSeek's model list | chunk 4 (O4) |
| `llm_config.yaml` `qwen:7b` vs installed `qwen3.5` | chunk 4 (GL.9 test) |
| `query_builder.py:55` comment is wrong | chunk 1 |
| Memory note "Status: COMPLETE" describes the PyQt build | update project memory after this plan is approved |

---

## 6. Risks

- **Chunk 3 removes security code** (access code, sessions). Chunk 2's GL.2 must
  be merged and gated first, so there is never a commit where the API is open.
- **Keyring on Windows CI** may behave differently from a user's machine; GL.4's
  INT job covers the runner only, the HUMAN install check covers a real PC.
- **Parity scope creep.** The GL.5 table is the whole scope; new ideas go to
  `TODO.md`, not into chunks 5a–5c.
- **Retrieval re-baselines** (chunks 1, 8, 12, 13) each widen what the drift test
  accepts; each commit names the file and the reason, one item at a time.
