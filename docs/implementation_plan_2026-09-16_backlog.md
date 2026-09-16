# Implementation plan — the backlog after the web-app build

**Status:** awaiting approval. No code written.
**Source:** `TODO.md`, the six gate files in `docs/cycles/`, and the live smoke
test run on 2026-09-16.

---

## 0. Reconciliation — what is already done

`TODO.md` was written incrementally and several items were fixed later the same
night. Each was re-checked against the code on 2026-09-16 rather than taken from
the list (learnings P6). These leave the backlog; batch A's first commit prunes
them from `TODO.md`.

| Item | Evidence it is done |
|---|---|
| `JobRegistry.get()` collapsing unknown / expired / other-user | `JobLookup.EXPIRED` in `src/jobs.py`; `test_an_expired_job_is_410_not_404` |
| Job ownership opt-in | `owner` has no default on `lookup`/`get`/`cancel`; `test_lookup_requires_an_owner` |
| Two failure contracts at the resolver | route runs `_coerce_summary()` on every provider; `test_an_ollama_summary_with_empty_fields_is_an_error_not_a_blank_card` |
| Vacuous `delenv(...) is None` assertion | removed from `tests/web/test_crypto.py` |
| `last4()` unwired | called from `src/user_store.py:set_llm_key` |
| Route-auth test was a floor | `checked == PROTECTED_ROUTE_COUNT` |
| `_run_migrations` bare `except: pass` | replaced by `_add_column_if_missing` |
| Container image never built | built and run on 2026-09-16 on a root-owned, non-empty volume: chown ran, uvicorn is PID 1 as uid 10001, data survived a restart |

---

## 1. Found today — not low; recommended first

The smoke test summarized 4 real Europe PMC papers on the owner Anthropic key.
Two succeeded. The two failures exposed these.

### N1 (HIGH) — a paper without a DOI can never be stored

`papers.doi` is `TEXT UNIQUE NOT NULL` and `insert_paper()` writes `doi or None`,
so every DOI-less record fails the NOT NULL constraint and returns `None`.
Measured: two different DOI-less papers → `None`, `None`.

Consequence: **every arXiv record** (the adapter sets `doi=""`), many PubMed and
PsyArXiv records, and some Europe PMC records. In the web app their summaries are
billed and not saved (`_paper_row_id` logs a warning); in the desktop GUI
"save to database" skips them without saying so.

- **Fix:** identity is `canonical_id`, which every adapter sets. Additive
  migration: a new unique index on `canonical_id`, and a table rebuild to drop
  `NOT NULL` from `doi` (SQLite cannot alter a column constraint in place).
  `insert_paper` stores `NULL` for a missing DOI — `UNIQUE` permits many NULLs.
  `_paper_row_id` and `/api/summaries/lookup` fall back to `canonical_id`.
- **Risk:** a table rebuild on a populated database. Done inside one
  transaction, with row counts compared before and after (P8, P33).
- **Tests:** `test_n1_two_doi_less_papers_are_both_stored`,
  `test_n1_migration_preserves_every_row_on_a_populated_db` (real-scale fixture,
  exact before/after count), `test_n1_arxiv_summary_is_saved_and_looked_up`.

### N2 (MEDIUM) — web summaries do not recover a missing abstract

Paper 1 in the smoke test was open access with full text on PMC, but its search
record carried no abstract and no PDF URL, and the summary path only tries a
PDF. The desktop app's `AbstractFetchWorker` already recovers abstracts (Europe
PMC by DOI, PMC full-text, Crossref, OpenAlex, page scrape). Phase 0 extracted
`fetch_openalex_abstract` and `scrape_abstract_from_url` precisely so the web app
could use them — and nothing calls them there (P21).

- **Fix:** move `AbstractFetchWorker`'s strategy chain out of `gui.py` into
  `src/paper_meta.py:recover_abstract(paper)`; the GUI worker and
  `_extract_text` both call it. Recovery runs only when both abstract and PDF
  text are empty, and is recorded on the job phase.
- **Adjacent:** paper 3 was a "Correction to:" notice. Refusing it was right;
  the error should say "this is a correction notice, not an article" rather
  than "no text".
- **Tests:** `test_n2_missing_abstract_is_recovered_before_summarizing`,
  `test_n2_gui_worker_and_web_route_use_the_same_recovery` (identity check,
  as for `filter_papers`), `test_n2_correction_notice_gets_a_specific_message`.

---

## 2. Medium items still open

Listed so this plan does not silently narrow to "low". Batched with the query
builder work (batch D) because they touch the same files.

| ID | Item |
|---|---|
| M1 (was F1) | arXiv adapter retries 429 but not 5xx/timeouts → treated as terminal |
| M2 (was F2) | `monitor.py` PDF download failures silent (DEBUG, return value discarded) |
| M3 (was F3) | arXiv applies the author filter at query time, stricter than the client-side match → **decision needed**, §5 |

---

## 3. The low findings, by batch

Each batch is one commit and one `/chdp`. Every test named below is written
first and mutation-checked.

### Batch A — dead code and unwired helpers (P21)

| Item | Fix | Test |
|---|---|---|
| `crypto.mask()` has no caller | delete it; `last4()` is what the UI uses | existing masking tests move to `last4` |
| `import _source_label` unused in `routes_searches.py` | delete | — (flake8 in CI, batch H) |
| `AppContext._extras` unused | delete | — |
| `JobRegistry.jobs_for()` unused outside tests | delete with its test | — |
| Prune the done items (§0) from `TODO.md` | | |

### Batch B — `src/llm.py` legacy (desktop summarizer)

| Item | Fix | Test |
|---|---|---|
| `# Limit to first 3000 chars…` is inside the prompt string | move the comment out | `test_b_prompt_contains_no_source_comment` (builds the prompt) |
| 3000-char truncation silent (P9/L7) | use `llm_config.max_text_chars`, log "X of Y kept" | `test_b_truncation_is_announced` |
| `OLLAMA_MODEL = "qwen:7b"` hardcoded (L1) | read from `llm_config.yaml` | `test_b_ollama_model_comes_from_config` |
| Qwen text parser still returns blanks on an unrecognised shape | route through `_coerce_summary` so the desktop agent matches the web app | `test_b_desktop_agent_rejects_a_blank_summary` |

Note: your installed Ollama model is `qwen3.5`, not `qwen:7b`. With the model in
config, that becomes a one-line edit.

### Batch C — deprecation class (P5)

| Item | Fix | Test |
|---|---|---|
| `datetime.utcnow()` — 11 call sites across adapters and `cache.py` | `datetime.now(timezone.utc)` everywhere, one commit | `test_c_no_utcnow_remains` (AST scan, not substring) and the suite runs with `-W error::DeprecationWarning` for `src/` |

**Touches the protected retrieval layer.** `test_no_retrieval_drift` will go red
by design; the same commit updates `BASELINE` and says why (W1.a allows a change
that is required and flagged).

### Batch D — arXiv query builder and the monitor CLI

| Item | Fix | Test |
|---|---|---|
| arXiv wildcards passed through (`all:adolescen*`) | strip trailing `*` as the PsyArXiv builder does, and log it | `test_d_arxiv_query_strips_wildcards` |
| arXiv version discarded; "2+ revised" can never match | carry version from the id into `CanonicalRecord` | `test_d_arxiv_v2_matches_revised_only_filter` |
| `all:` broader than Europe PMC's bare term | document in `query_builder.py` and README | — |
| CLI exits 0 when sources failed | exit 2 and name the sources on stderr | `test_d_monitor_exit_code_reflects_failed_sources` |
| Duplicate filter names silently dropped by `--all` | warn and keep both (key by index) | `test_d_duplicate_filter_names_both_run` (uses real `filters.json`) |
| `dict \| None` breaks the 3.9 floor | `Optional[dict]` — or raise the floor, §5 | `test_d_monitor_imports_on_the_declared_floor` (CI matrix) |
| M1, M2, M3 | as §2 | `test_d_arxiv_retries_5xx_with_backoff`, `test_d_monitor_counts_failed_downloads` |

Also touches the protected layer; same `BASELINE` handling as batch C.

### Batch E — tests that check the wrong thing

| Item | Fix | Test |
|---|---|---|
| Constant-time comparison asserted via bytecode names | patch `hmac.compare_digest`, sign in, assert it was called with the code | replaces `test_the_access_code_is_compared_in_constant_time` |
| Env-var documentation test misses variables read through indirection (`api_key_env`, `model_env` in `llm_config.yaml`) | also collect names from `llm_config.yaml` | `test_e_env_example_covers_config_declared_variables` |

### Batch F — container and access hardening

| Item | Fix | Test |
|---|---|---|
| `fatal()` hint says "writable by biorx" on the non-root branch | pass the uid into the hint | extend `test_the_entrypoint_refuses_to_start_on_an_unwritable_volume` |
| No Content-Security-Policy | `default-src 'self'; frame-ancestors 'none'` via middleware; the client already has no inline script | `test_f_csp_header_on_every_response`, plus a browser check that the page still works |
| Unbounded users and jobs | cap concurrent jobs per user (default 3) and new sessions per hour | `test_f_fourth_concurrent_search_is_refused`, `test_f_session_creation_is_rate_limited` |
| Stored-summary route returns `created_by_user_id` | **decision**, §5 | — |

### Batch G — database and threading lows

| Item | Fix | Test |
|---|---|---|
| `release()` only in `SearchWorker` | add to the download, summarize and abstract-fetch workers' `finally` | one boundary test per worker, as for `SearchWorker` |
| `check_same_thread=False` hides a future locality regression | keep it (the finalizer needs it), add a debug-mode assertion in `conn` that the caller's thread owns the holder | `test_g_cross_thread_use_is_detected` |
| `_release_connection` catches only `sqlite3.Error` | catch `Exception` at shutdown only | `test_g_release_survives_a_non_sqlite_error` |

### Batch H — environment and CI

| Item | Fix | Test |
|---|---|---|
| No `.github/workflows/tests.yml` (standing rule in `~/.claude/CLAUDE.md`) | install `requirements-web.txt`, run the suite on a blank machine, Python matrix per §5 | the workflow itself; every assertion stays in the suite |
| Two local interpreters (3.11 test, 3.12 app); GUI tests always skip under the documented command | **decision**, §5 | — |
| Personal contact addresses in `src/sources/config.py` and `arxiv.py` | read from `BIORX_CONTACT_EMAIL`; `sources_config.yaml` keeps the real value | `test_h_no_personal_address_in_source` (AST string constants) |

Protected-layer note as batch C.

### Batch I — outside this repo

`~/.claude/standards/llm-integration.md` L1 lists stale "current" Anthropic model
ids (`claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5`). Current:
`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`. A separate commit in the
`claude-standards` repo, pulled first and pushed from there.

---

## 4. Order

| # | Batch | Why here |
|---|---|---|
| 1 | N1 | Every DOI-less summary is lost today; highest impact (P10) |
| 2 | N2 | Same code path; the smoke test already reproduces it |
| 3 | H (CI only) | Every later batch then gets a blank-machine run |
| 4 | A | Cheap, and shrinks the surface later batches touch |
| 5 | B | Desktop summarizer; independent |
| 6 | E | Test quality before more tests are written on top |
| 7 | D | Largest; needs the M3 decision |
| 8 | C | Mechanical once D has changed the same adapters |
| 9 | F | Needs the `created_by_user_id` decision |
| 10 | G | Desktop threading; lowest risk to users |
| 11 | H (rest), I | Housekeeping |

---

## 5. Decisions needed

1. **M3 — arXiv author filter.** (a) drop the query-time author clause and let
   the client-side filter own author matching — recommended, since it is the
   only source that filters authors at query time; or (b) loosen the clause.
2. **Python floor.** `CLAUDE.md` says 3.9+, but the web dependencies and
   `dict | None` already need 3.10+. Recommend declaring **3.11+** and making CI
   test 3.11 and 3.12.
3. **Local interpreters.** Install PyQt6 into 3.11 so the documented command
   runs the GUI tests, or change the documented command to 3.12. Recommend the
   latter — 3.12 is what the app and the container run.
4. **`created_by_user_id` in shared summaries.** Keep (colleagues can see who
   ran a summary) or strip from the response. Recommend strip — the database
   keeps it for provenance either way.
5. **Per-user limits** in batch F: 3 concurrent jobs, 10 new sessions per hour
   per IP. Change the numbers or accept.

## 6. Out of scope

From the cold review, features rather than defects: persisting search results
beyond the one-hour job TTL, and surfacing the existing bookmarks and search
history tables in the web client. Worth a spec of their own.
