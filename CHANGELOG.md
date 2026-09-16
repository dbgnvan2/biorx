# Changelog

## 2026-09-15 — pluggable LLM backends and background jobs

### Added
- `llm_config.yaml` + `src/llm_config.py` — provider dialects, model ids, the
  paper-text budget and the owner-key spend cap, in configuration rather than
  source. `LLM_PROVIDER`, `ANTHROPIC_MODEL`, `DEEPSEEK_MODEL` and
  `SUMMARY_DAILY_CAP_PER_USER` override the file.
- `src/llm_providers.py` — `DeepSeekClient` (OpenAI-compatible dialect),
  `AnthropicClient` (official SDK), and `resolve_client()` with the precedence
  user key → owner key → a typed error naming both what the user should do and
  what the operator should set. Hosted replies are requested as JSON against a
  schema and validated, so a reply in the wrong shape raises instead of storing
  a blank summary.
- `src/crypto.py` — Fernet encryption of a user's API key, plus masking.
  `KEY_ENC_SECRET` is required from the environment and never generated.
- `src/jobs.py` — an in-process job registry for searches and summaries, which
  take minutes and cannot be held open by an HTTP request. Owner-scoped,
  cancellable, expiring, and guarded so a worker can never leave a job on
  "running".
- `requirements-web.txt`, separate from `requirements.txt` so PyQt6 is never
  installed in the container.

### Note
The Anthropic default model is `claude-sonnet-5`. `claude-sonnet-4`, which the
original brief specified, is not a real model id.

## 2026-09-15 — web app groundwork

### Added
- `src/paper_meta.py` — PDF/landing-page URL resolution and abstract recovery
  (JSON-LD, meta tags, HTML patterns, OpenAlex inverted index), extracted from
  `gui.py` so a second front end can reuse it.
- `src/filters_store.py` — filters.json persistence and the pure predicates
  `filter_is_enabled()` / `filter_has_text()`.
- Database tables for the planned web app: `users`, `user_filters`,
  `usage_events`; `summaries.created_by_user_id` for provenance.
- `Database.release()` and automatic per-thread connection release.
- `docs/implementation_plan_2026-09-15.md` — the approved web-app plan.

### Changed
- **`Database` gives each thread its own connection.** The single shared
  connection was justified by "writes are always serial (one worker at a time)",
  which is true of a desktop GUI and false of a web app. WAL journal mode, a
  busy timeout from `BIORX_DB_BUSY_TIMEOUT_MS`, and `close()` now closing every
  outstanding handle.
- Database and contact configuration is environment-driven: `BIORX_DB_PATH`,
  `DATA_DIR`, `BIORX_CONTACT_EMAIL`. Nothing resolves to a developer's home
  directory in a container or a test.
- `insert_summary()` records `created_by_user_id` alongside `model_version`.

### Fixed
- Per-thread connections are released when their thread exits — including on
  **QThread**, where `threading.current_thread()` is a `_DummyThread` whose
  `is_alive()` never goes False. Release is keyed on the lifetime of the
  thread-local holder via `weakref.finalize`, so it does not depend on
  thread-object semantics. `SearchWorker` also releases explicitly.
- Schema migrations read `PRAGMA table_info` instead of wrapping every
  `ALTER TABLE` in `except Exception: pass`, which made a real migration failure
  indistinguishable from "column already exists".

## 2026-09-15

### Added
- **arXiv source** (`src/sources/arxiv.py`) — covers the CS / LLM-agent /
  agent-based-simulation literature none of the other eight sources index. Atom
  XML parsing, descriptive User-Agent, >= 3s request spacing, `Retry-After`
  honoured on 429. Registered in the orchestrator and the source picker.
- **arXiv query builder** — `build_arxiv_query()` mirrors the Europe PMC group
  semantics with arXiv field syntax (`ti:` / `abs:` / `all:` / `au:`) plus a
  `submittedDate` range.
- **Headless CLI** (`agents/monitor.py`) — runs saved filters without PyQt6 and
  emits JSONL, so cron can drive the multi-source layer.
- **`Agent Simulation` filter** in `filters.json` — six facets covering agent
  architecture, algorithmic fidelity, persona/emotion, self-adapting models,
  emergent misalignment, and computational behaviour.
- `CHANGELOG.md` and `TODO.md`.

### Changed
- **`src/filtering.py` is new and now owns a saved filter's client-side
  semantics**, extracted from `gui.py`. The GUI imports it under its former
  private names; the CLI applies it too, so the same filter no longer returns
  different sets on different front ends.
- The orchestrator's last-page detection uses the source's own page size when an
  adapter reports one, instead of the number of records it returned.

### Fixed
- `authors` in a filter is read through `normalize_authors()`, which accepts both
  the list the GUI writes and the comma-separated string older filters use.
  Previously the arXiv query builder crashed on a list, and the client-side
  filter iterated a string character-by-character and matched nearly everything.
- A withdrawn arXiv paper on a full page no longer truncates the search: dropping
  it shortened the returned page, which the orchestrator read as "last page".
- Withdrawal detection is an anchored match on arXiv's conventional notice rather
  than a bare `"withdrawn"` substring, which deleted real papers whose abstracts
  merely mention withdrawal. Drops are counted and logged.
- arXiv version suffixes are stripped by regex, so old-style identifiers such as
  `cs.CV/0701001v1` produce a correct `canonical_id`.
