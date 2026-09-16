# Implementation plan — BioRx as a small private web app

**Spec:** "Task: Make BioRx shareable as a small private web app" (chat, 2026-09-15)
**Status:** APPROVED 2026-09-15. Implementation proceeding.
**Decisions already given:** searches run as **async jobs**; users are identified by a
**server-issued, cookie-bound id** (not a typed display name).

---

## 0. Spec IDs

The spec carries no hierarchical IDs, so this plan assigns them and quotes the source text
verbatim beside each. `W<n>` maps to the spec's "Hard requirements" heading `<n>`; `D<n>` to
its "Definition of done" bullets, in order.

| ID | Verbatim spec text |
|---|---|
| W1 | "Do not touch the existing retrieval/agent logic" |
| W1.a | "The orchestrator, dedup, query builder, and adapters stay exactly as they are. Wrap them, don't modify them, unless a change is strictly required to expose a route — and if so, flag it explicitly." |
| W1.b | "Existing tests in `tests/` must stay green" |
| W2 | "Pluggable LLM backend (extend `src/llm.py`)" |
| W2.a | "`DeepSeekClient` — OpenAI-compatible API, base URL `https://api.deepseek.com`, default model `deepseek-chat`, key from `DEEPSEEK_API_KEY`." |
| W2.b | "`AnthropicClient` — Anthropic Messages API, default model configurable via `ANTHROPIC_MODEL` (default `claude-sonnet-4`), key from `ANTHROPIC_API_KEY`." |
| W2.c | "Keep `OllamaClient` for local dev (`http://localhost:11434`, `qwen:7b`)." |
| W2.d | "Add a **provider resolver** with this precedence: the requesting user's own key (if they supplied one) → the server owner key → error telling the user to add a key." |
| W3.a | "**(a) Owner-supplied keys**: set via env vars… these are the default for every user who hasn't added their own key." |
| W3.b | "**(b) Bring-your-own key**: a colleague can paste their own DeepSeek or Anthropic API key in the UI." |
| W3.c | "Keys are never logged, never returned to the client in full (mask to last 4 chars in the UI), and never committed (ensure `.env` / secrets are gitignored)." |
| W3.d | "Store the key in SQLite; encrypt at rest with Fernet using a server-side secret from env (`KEY_ENC_SECRET`), generating one on first run if absent. If encryption is deemed overkill for this scale, say so explicitly in the plan and store it out-of-band instead." |
| W4.a | "One shared `ACCESS_CODE` env var. Entering it sets a signed session cookie (itsdangerous)." |
| W4.b | "Lightweight identity: on first use, a colleague optionally enters a display name so their BYO key is tied to them. No passwords — the access code is the gate." |
| W5.a | "Single-page HTML + vanilla JS served as static files by FastAPI (no React/Vite build step; keep it one deployable container)." |
| W5.b | "Search box…, results table with pagination, per-paper buttons: Download PDF, Summarize, and a small 'LLM settings' panel for entering/removing a personal API key." |
| W5.c | "Match the concepts of the existing `gui.py` screens; do not add features beyond what the desktop app already does." |
| W6 | "Reuse the existing SQLite schema from `src/db.py`. Add only what's needed… Use parameterized queries — no string-built SQL." |
| W7.a | "A `Dockerfile` + `railway.json` (or equivalent) that runs the FastAPI app with uvicorn. SQLite and any PDFs go on a persistent volume." |
| W7.b | "`.env.example` documenting every env var (LLM_PROVIDER, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ACCESS_CODE, KEY_ENC_SECRET)." |
| D1 | "Existing tests pass; any new backend logic has tests." |
| D2 | "Summarization works in all three modes: owner key only, user BYO key overriding owner key, and no key (clean error, no crash)." |
| D3 | "No secrets in source or logs; keys masked in the UI." |
| D4 | "`README` updated with how to run locally and deploy." |

---

## 1. Deviations from the spec, and why

Each of these changes something the spec states. None proceeds without your approval.

### 1.1 `claude-sonnet-4` is not a current model id (changes W2.b)

Verified against the bundled `claude-api` reference (model table cached 2026-06-24). Current
Anthropic ids are `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5` — no date suffixes.
`claude-sonnet-4` would 404 on the first call.

**Proposed:** `ANTHROPIC_MODEL` defaults to `claude-sonnet-5` for a summarization workload
(cheaper per token than Opus and adequate for structured extraction); `claude-opus-5` stays
available by setting the env var. Model ids live in `llm_config.yaml`, never in source (L1).

**Also flagged:** `~/.claude/standards/llm-integration.md` L1 lists "current" ids as
`claude-opus-4-8` / `claude-sonnet-4-6` / `claude-haiku-4-5`. That list is now stale and is
probably where `claude-sonnet-4` came from. It lives in the separate `claude-standards` repo;
updating it is a separate change I have not made.

### 1.2 `KEY_ENC_SECRET` must not be generated on first run (changes W3.d)

Encryption is **not** overkill — it is cheap and it defends the realistic case (a copied
volume, a backup, a `SELECT *` during support). But "generating one on first run if absent"
writes the key to the same disk as the ciphertext, which makes it decorative.

**Proposed:** `KEY_ENC_SECRET` is required from env. If it is absent, the app starts normally
but **BYO key storage is disabled** and the UI says so explicitly; it never silently generates
a secret next to the data. Startup logs the effective state (`byo_keys: enabled|disabled`).

### 1.3 Identity is a server-issued id; the display name is a label (changes W4.b)

As agreed. A shared access code plus a self-declared name means anyone who has the code can
type a colleague's name and spend that colleague's API key. The cookie carries an opaque
`user_id` from `secrets.token_urlsafe(16)`, signed with `itsdangerous`; `display_name` is a
mutable label with no authority.

### 1.4 A spend cap is added (not in the spec)

W3.a makes the owner key the default for every user who has the access code, with no ceiling.
The access code will end up in a chat thread. **Proposed:** `SUMMARY_DAILY_CAP_PER_USER`
(default 25), counted only against summaries billed to the **owner** key; a user on their own
key is uncapped. Exceeding it returns a clean 429 with the reason.

### 1.5 PDFs are not proxied or stored by default (narrows W5.b / W7.a)

Every record the pipeline returns is open access with a publisher `pdf_url`. "Download PDF"
becomes a link/redirect to that URL rather than a server-side fetch, which removes unbounded
disk growth, a slow proxy route, and a second egress path. PDFs are fetched to the volume
**only** when a summarize job needs the text, under `DATA_DIR/pdfs`.

**Decision needed:** accept, or proxy downloads through the server (costs disk + a job).

### 1.6 `src/db.py` must change — flagged per W1.a

`Database` holds one shared `sqlite3` connection with `check_same_thread=False`, and its safety
comment reads: "Writes are always serial (one worker at a time) so this is safe." A multi-user
web app voids that premise on day one. Required change: per-thread connections, WAL mode, and a
`busy_timeout`. `src/db.py` is persistence, not retrieval — the orchestrator, dedup, query
builder and adapters are untouched (W1.a holds).

### 1.7 Pre-work: logic still living in `gui.py`

The spec's premise — "the search/summarization logic is already decoupled and headless" — is
not yet true. `_filter_papers` was extracted today; still in `gui.py` and needed by any web
backend: `_scrape_abstract_from_url` (gui.py:282), `_fetch_openalex_abstract` (gui.py:376),
`_pdf_url` (gui.py:242), `load_filters`/`save_filters` (gui.py:93). Phase 0 extracts these to
`src/` before any route is written, so the web app does not grow a second copy of each.

---

## 2. Architecture

### 2.1 New modules

| File | One-sentence responsibility (file-maintainability §1) |
|---|---|
| `src/paper_meta.py` | Resolve a paper's PDF URL and recover a missing abstract (extracted from `gui.py`). |
| `src/filters_store.py` | Read/write saved filters — file-backed for the desktop app, DB-backed per user for the web app. |
| `src/llm_config.py` | Load `llm_config.yaml`: provider dialects, model ids, timeouts, truncation limits. |
| `src/llm_providers.py` | `DeepSeekClient`, `AnthropicClient`, and `resolve_client()` — the precedence chain. |
| `src/crypto.py` | Fernet wrap/unwrap of a user API key, plus `mask()`. |
| `src/jobs.py` | In-process job registry: submit, poll, cancel, expire. No HTTP, no DB. |
| `web/app.py` | FastAPI application factory and route registration. |
| `web/auth.py` | Access-code check, signed cookie, `current_user` dependency. |
| `web/routes_*.py` | One module per resource: `session`, `filters`, `searches`, `summaries`. |
| `web/static/{index.html,app.js,styles.css}` | The SPA. |

`src/llm.py` keeps `OllamaClient` + `MockOllamaClient` unchanged (W2.c); the new clients live
in `src/llm_providers.py` rather than growing `llm.py` into a second responsibility.

### 2.2 Route list

All `/api/*` routes except `POST /api/session` require a valid session cookie (S3).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/session` | Exchange `ACCESS_CODE` for a signed cookie carrying a new or existing `user_id`. |
| DELETE | `/api/session` | Log out. |
| GET | `/api/me` | `{user_id, display_name, provider, key_source, key_last4, byo_enabled, cap_remaining}`. |
| PATCH | `/api/me` | Set `display_name` (label only). |
| PUT | `/api/me/llm-key` | Store this user's key, encrypted. Returns masked only. |
| DELETE | `/api/me/llm-key` | Remove it. |
| GET | `/api/filters` | This user's saved filters. |
| POST/PUT/DELETE | `/api/filters[/{id}]` | Saved-filter CRUD. |
| POST | `/api/searches` | `202 {job_id}` — starts an async search. |
| GET | `/api/searches/{job_id}` | `{status, phase, fetched, total, matched, error}`. |
| GET | `/api/searches/{job_id}/results?offset&limit` | Paginated matched records. |
| DELETE | `/api/searches/{job_id}` | Cancel (drives the orchestrator's `should_stop`). |
| POST | `/api/summaries` | `202 {job_id}` — download → extract → summarize. |
| GET | `/api/summaries/{job_id}` | Job status. |
| GET | `/api/papers/{canonical_id}/summary` | The stored summary, if any. |
| GET | `/healthz` | Liveness + effective config (never secrets). |

### 2.3 Job model

`src/jobs.py` holds a `JobRegistry` over a bounded `ThreadPoolExecutor` (default 4 workers),
job records in a lock-guarded dict, statuses `queued | running | done | error | cancelled`, and
a TTL sweep (default 1 h). Search jobs wrap `SourceOrchestrator.search()` and feed its existing
`on_batch` / `on_progress` / `on_status` / `should_stop` callbacks — no orchestrator change
(W1.a). Results are applied through `src.filtering.filter_papers` exactly as the GUI and CLI do.

Two rules the registry enforces, from the failure catalogue:
- **P15:** every worker body — *including setup* — is wrapped in `try/except Exception` that
  writes `status="error"` with the message. A worker may never exit leaving `running`.
- **P2:** per-source failures the orchestrator swallows are surfaced on the job record as
  `sources_failed: [...]`, so "0 results" and "arXiv was down" are distinguishable.

**Accepted limitation:** jobs are in-process and die on redeploy. For a few colleagues this is
the right trade against adding Redis/Celery. The UI treats an unknown `job_id` as "expired —
run again", and the README says so.

### 2.4 Provider resolver (W2.d)

```
resolve_client(user_id, db, cfg) -> Resolved(client, provider, model, key_source)
  1. user row has a decryptable key         -> key_source="user"
  2. env key for cfg.default_provider       -> key_source="owner"   (cap applies)
  3. raise NoLLMCredentialError             -> route returns 400 + "add a key in LLM settings"
```

Dialect comes from `llm_config.yaml`, never inferred from the model name (L8): Anthropic uses
the official pinned `anthropic` SDK; DeepSeek uses `requests` against the OpenAI-compatible
`/chat/completions`. `NoLLMCredentialError` is a typed exception, not a sentinel string (P14),
because `OllamaClient.generate()` currently returns `None` for every failure and D2 requires a
*clean, specific* error.

**Output parsing is the biggest technical risk (P19).** `summarize_paper()` parses Qwen's text
by splitting on `\n\n` and matching `KEY FINDINGS:` / `METHODOLOGY:` / `CONCLUSIONS:` prefixes.
Anthropic and DeepSeek will not reliably emit that shape, and the parser fails *quietly* — it
returns a dict with empty fields rather than raising. Plan: request structured output from both
API providers (Anthropic `output_config.format`, DeepSeek JSON mode), keep the text parser only
for Ollama, and make an empty parse a loud `failed_to_parse` state (L4) rather than a stored
blank summary.

### 2.5 Schema migration (W6)

Additive only, in the existing `_run_migrations` style, parameterised queries throughout.

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id            TEXT PRIMARY KEY,        -- secrets.token_urlsafe(16)
    display_name       TEXT    DEFAULT '',
    llm_provider       TEXT    DEFAULT '',
    llm_key_ciphertext BLOB,
    llm_key_last4      TEXT    DEFAULT '',
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at       TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL, name TEXT NOT NULL,
    filter_json TEXT NOT NULL, enabled BOOLEAN DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name), FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL, kind TEXT NOT NULL,       -- 'summary'
    provider TEXT, model TEXT, key_source TEXT,      -- 'user' | 'owner'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- summaries: add created_by_user_id TEXT
```

`summaries.paper_id` is `UNIQUE` and `insert_summary` uses `INSERT OR REPLACE`, so one summary
exists per paper and the next user's run overwrites the previous one. **Decision needed:** keep
that (colleagues share one summary per paper — simplest, and arguably what you want) or key on
`(paper_id, model)` so different backends coexist. Plan assumes **keep**, recording
`created_by_user_id` and the resolved `model_version` so provenance is visible.

### 2.6 Paths and configuration

Everything the app writes is env-driven, so nothing resolves to a developer's home directory
in a container or a test (P34): `DATA_DIR` (default `./data` locally, `/data` on the volume)
determines `BIORX_DB_PATH` and the PDF directory; `Database()` and `PDFHandler()` keep their
current defaults for the desktop app but the web app always passes explicit paths.

`requirements-web.txt` is separate from `requirements.txt` — PyQt6 must not be installed in the
container. New pinned deps (S7): `fastapi`, `uvicorn[standard]`, `itsdangerous`, `cryptography`,
`anthropic`.

---

## 3. Acceptance criteria → tests

Every row names the automated test that verifies it. `tests/web/` is a new package.

| ID | Criterion (abbrev.) | Test |
|---|---|---|
| W1.a | Retrieval modules unmodified | `tests/web/test_no_retrieval_drift.py::test_retrieval_modules_unchanged_by_this_branch` — asserts `git diff` against the pre-web baseline touches none of `orchestrator.py`, `dedup.py`, `query_builder.py`, `src/sources/*adapter*` |
| W1.b | Existing tests stay green | CI job runs the full suite; `test_suite_baseline_count` asserts the pre-existing test count has not fallen |
| W2.a | DeepSeek client shape | `tests/web/test_providers.py::test_deepseek_uses_openai_dialect_and_bearer_auth` (mocked HTTP; asserts URL, header, body shape) |
| W2.b | Anthropic client shape + model id | `::test_anthropic_uses_messages_api_with_configured_model`, `::test_default_anthropic_model_is_a_current_id` |
| W2.c | Ollama unchanged | `::test_ollama_client_interface_unchanged` (signature + base URL + model) |
| W2.d | Resolver precedence | `::test_resolver_prefers_user_key_over_owner_key`, `::test_resolver_falls_back_to_owner_key`, `::test_resolver_raises_typed_error_when_no_key` |
| W3.a | Owner key is the default | `tests/web/test_summaries.py::test_user_without_key_is_billed_to_owner_key` |
| W3.b | BYO key stored per user | `tests/web/test_llm_key_routes.py::test_put_key_then_resolver_uses_it` |
| W3.c | Never logged, never returned in full | `::test_key_is_never_returned_in_full` (response body scan), `::test_key_never_appears_in_logs` (caplog scan across store/resolve/summarize), `tests/web/test_secrets_hygiene.py::test_repo_contains_no_key_shaped_literals` |
| W3.d | Fernet at rest; no auto-generated secret | `tests/web/test_crypto.py::test_key_roundtrips_through_fernet`, `::test_stored_ciphertext_does_not_contain_plaintext`, `::test_missing_enc_secret_disables_byo_storage_without_generating_one` |
| W4.a | Access code gate + signed cookie | `tests/web/test_auth.py::test_wrong_access_code_is_rejected`, `::test_cookie_is_signed_and_tamper_evident`, `::test_every_api_route_requires_a_session` (enumerates the app's routes — exact count, not a floor, per P29) |
| W4.b | Identity is server-issued | `::test_display_name_change_does_not_change_user_id`, `::test_typing_another_users_display_name_does_not_reach_their_key` |
| W5.a | Static SPA, no build step | `tests/web/test_static.py::test_index_is_served_and_references_only_local_assets` |
| W5.b | Controls reach the backend | `tests/web/test_frontend_wiring.py` — one test per control asserting the value arrives at the boundary function (P25): search filter, page size, Summarize, LLM-key save/remove |
| W5.c | Matches gui.py concepts | **Not code-testable — see §4** |
| W6 | Additive schema, parameterised SQL | `tests/web/test_schema.py::test_migration_is_additive_on_a_populated_db` (dirty-state, P8), `::test_no_sql_built_by_string_formatting` (AST scan for f-strings/`%`/`+` inside `execute()`) |
| W7.a | Container runs the app | `tests/web/test_deploy_files.py::test_dockerfile_installs_web_requirements_not_pyqt`, `::test_railway_config_points_at_uvicorn_and_a_volume`; **live deploy is human-verified — §4** |
| W7.b | `.env.example` complete | `::test_env_example_documents_every_variable_the_code_reads` (scans `os.environ`/`getenv` call sites, asserts each appears) |
| D1 | New logic has tests | Coverage over `src/llm_providers.py`, `src/crypto.py`, `src/jobs.py`, `web/` |
| D2 | Three summarization modes | `tests/web/test_summaries.py::test_summary_with_owner_key_only`, `::test_user_key_overrides_owner_key`, `::test_no_key_returns_clean_error_and_does_not_crash` |
| D3 | No secrets in source or logs | as W3.c |
| D4 | README updated | `tests/web/test_docs.py::test_readme_documents_local_run_and_deploy` (asserts the sections and every env var are present) |
| — | Async jobs behave | `tests/web/test_jobs.py::test_worker_exception_sets_error_status_not_running` (P15), `::test_setup_failure_before_running_is_also_guarded`, `::test_cancel_stops_the_orchestrator`, `::test_expired_job_id_is_reported_as_expired` |
| — | Source failure is visible | `::test_failed_source_is_reported_on_the_job_not_silently_zero` (P2) |
| — | Spend cap | `tests/web/test_cap.py::test_owner_key_summaries_are_capped_per_user_per_day`, `::test_user_own_key_is_not_capped` |
| — | Summary parsing across providers | `tests/web/test_summary_parsing.py::test_each_provider_response_shape_parses_to_the_same_fields`, `::test_unparseable_response_is_failed_to_parse_not_a_blank_summary` (P19/L4) |
| — | DB under concurrency | `tests/web/test_db_concurrency.py::test_parallel_writes_from_multiple_threads_all_land` |

Every test above will be mutation-checked before the phase lands: delete or invert the line it
names, confirm red, restore (P27).

---

## 4. Criteria that cannot be made code-testable

| ID | Why | Proposed human review |
|---|---|---|
| W5.c "Match the concepts of the existing `gui.py` screens" | A judgement about UI equivalence. | Side-by-side checklist: Search & Browse and Configure screenshots against the web screens; you sign off that no concept is missing and nothing new was invented. |
| W7.a live deployment | Needs the Railway account, a volume, and real env vars. | Deploy checklist in the README: build, volume mounted at `/data`, `/healthz` returns the expected config, one real search, one real summary, restart and confirm the DB survived. |
| Real provider calls (DeepSeek, Anthropic) | Cost money and need keys; every suite test is mocked. | Flagged **integration-only, untested** in the suite (L9). One manual smoke per provider recorded in the README with date and model id. |
| "No secrets in source" (D3, whole-history) | The repo test covers the working tree only. | `git grep` per security.md S1 before the first push, plus `.env*` already in `.gitignore` (verified today). |

---

## 5. Implementation order

Each phase is one commit, ends green, and runs the full suite.

| # | Phase | Depends on | Notes |
|---|---|---|---|
| 0 | Extract `_pdf_url`, `_scrape_abstract_from_url`, `_fetch_openalex_abstract`, filters IO out of `gui.py` into `src/`; GUI imports them | — | Behaviour-preserving; GUI tests must stay green (§1.7) |
| 1 | `src/db.py`: per-thread connections, WAL, busy_timeout, env-driven paths; new tables | 0 | The one flagged W1.a exception (§1.6) |
| 2 | `src/crypto.py` + `src/llm_config.py` | 1 | No network; pure |
| 3 | `src/llm_providers.py`: two clients + resolver + structured output | 2 | Highest-risk unit (§2.4) |
| 4 | `src/jobs.py` | — | Independent of 1–3; can land in parallel |
| 5 | `web/auth.py` + `POST/DELETE /api/session`, `/api/me` | 1,2 | Auth before anything it guards |
| 6 | `/api/filters` | 5 | |
| 7 | `/api/searches` (async) | 4,5,6 | Wraps the orchestrator unchanged |
| 8 | `/api/summaries` + cap | 3,4,5 | |
| 9 | `web/static` SPA | 5–8 | Per-control wiring tests (P25) |
| 10 | `Dockerfile`, `railway.json`, `requirements-web.txt`, `.env.example` | 9 | |
| 11 | README + deploy checklist | 10 | D4 |
| 12 | Review: `learning-qa` over the whole diff, then one **cold** pass that does not know how the code was written (P26) | 11 | Per CLAUDE.md working process step 5 |

---

## 6. Risks

1. **Summary parser drift across providers (P19)** — highest. Mitigated by structured outputs
   and a per-provider round-trip test; residual risk is that real provider output differs from
   the recorded fixtures, which only a manual smoke will catch.
2. **Jobs die on redeploy.** Accepted; surfaced in the UI and README (§2.3).
3. **SQLite writer contention.** WAL + busy_timeout + short transactions. A few colleagues is
   well within SQLite's range; the risk is a long-held write transaction inside a job.
4. **The access code will be shared.** The spend cap is the mitigation; rotating the code
   invalidates every cookie, which is the intended blunt instrument.
5. **PyQt6 in the container.** Prevented by the separate requirements file and a test.
6. **Polling interval vs platform idle timeouts.** Poll at 2 s; never hold a request open.

---

## 7. Adjacent issues found, not fixed (CLAUDE.md rule 10)

Found while reading; **not** in scope, listed so they are not silently inherited.

1. `src/llm.py:104` — the prompt f-string contains `{full_text[:3000]}  # Limit to first 3000
   chars to avoid token limits`. The `#` is *inside* the string, so that comment is sent to the
   model as part of the prompt on every summarization.
2. Same line — a 3000-char truncation with no announcement of how much was dropped (P9, L7).
3. `src/llm.py:13` — `OLLAMA_MODEL = "qwen:7b"` hardcoded in source (L1).
4. `src/db.py:_run_migrations` — `except Exception: pass` around every `ALTER TABLE`, so a real
   migration failure is indistinguishable from "column already exists" (P2).
5. `summaries.paper_id UNIQUE` + `INSERT OR REPLACE` — see §2.5 decision.
6. `/opt/homebrew/bin/pytest` is Python 3.11 without PyQt6, so every GUI test is permanently
   skipped under the documented test command.
7. Review findings 5–10 from 2026-09-15 (CLI failure signal, arXiv wildcards, duplicate filter
   names, `dict | None` on the 3.9 floor, `all:` semantics) remain open.

---

## 8. Out of scope

Multi-tenancy beyond a shared access code; accounts, SSO, or password reset; editing
`sources_config.yaml` from the web UI; batch/scheduled searches from the web app (`agents/
monitor.py` keeps that job); any change to ranking, dedup, or enrichment behaviour.

---

## 9. Decisions — settled 2026-09-15

All five taken as recommended.

| # | Decision | Resolution |
|---|---|---|
| 1 | §1.1 `ANTHROPIC_MODEL` default | `claude-sonnet-5` |
| 2 | §1.2 `KEY_ENC_SECRET` | Required from env; never auto-generated. BYO storage disabled when absent. |
| 3 | §1.4 Spend cap | 25 owner-key summaries per user per day; own-key users uncapped. |
| 4 | §1.5 PDF download | Link/redirect to the publisher URL; server fetches to the volume only for summarization. |
| 5 | §2.5 Summary uniqueness | One shared summary per paper, overwritten, with `created_by_user_id` + `model_version` recorded. |
