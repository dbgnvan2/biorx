# Changelog

## 2026-09-16 — papers without a DOI (N1)

### Fixed
- **Papers without a DOI can now be stored.** `papers.doi` was `NOT NULL`, so
  every arXiv record and many PubMed and PsyArXiv records silently failed to
  save — in the web app their summaries were paid for and lost, and in the
  desktop app "save to database" skipped them. Identity is now `canonical_id`,
  with a unique index; a paper with neither identifier is refused with a
  warning.
- Looking up an existing summary finds DOI-less papers, so re-opening an arXiv
  summary no longer re-runs (and re-bills) the model.

### Migration
Existing databases are rebuilt once, on first open, to drop `NOT NULL` from
`doi`. It runs in a single transaction with a row-count check, preserves every
column and the id counter, and refuses to run on a table definition it does not
recognise. Rehearsed on a copy of the live database (2,623 papers): identical
content, 0.04s.

## 2026-09-16 — the web client and deployment

### Added
- **A single-page client** (`web/static/`): vanilla HTML, CSS and JS with no
  build step, so the whole app is one deployable container that works offline.
  Saved searches with Run, a manual search form, a paginated results table with
  per-paper PDF and Summarize, and an LLM settings panel. It mirrors the desktop
  app's screens and adds nothing beyond them.
- **`Dockerfile`, `docker-entrypoint.sh`, `.dockerignore`, `railway.json`** and
  a documented `.env.example`. The image installs `requirements-web.txt` only,
  runs the app as an unprivileged user, and writes solely to the mounted volume.
- **README** sections on running locally, how access and credential precedence
  work, why searches are jobs, deploying to Railway, and a manual checklist for
  what CI cannot test.

### Fixed
- A search that matched nothing now says how many papers were fetched and
  filtered out. "0 matching" alone cannot be told apart from "the sources
  returned nothing".
- Link URLs from external APIs pass through a scheme check, so a
  `javascript:` URL in a paper record cannot run on click.
- The placeholder `ACCESS_CODE` from `.env.example` is treated as unset rather
  than as a live credential that is readable on GitHub.
- The container makes its mounted volume writable at **runtime**. A build-time
  `chown` does not survive a volume mount, so without this the first database
  write fails on startup. The app also refuses to start, naming the directory
  and uid, rather than surfacing a bare sqlite error later.

### Known
The container image has never been built: no Docker daemon was running on the
development machine. The first `docker build` is the deployer's, and the README
says so.

## 2026-09-15 — the web app backend

### Added
- **A FastAPI backend** (`web/`) so colleagues can use the retrieval pipeline
  from a browser. One shared `ACCESS_CODE` is exchanged for a signed cookie
  carrying a server-issued opaque user id; the display name is a label with no
  authority, so nobody can assume a colleague's identity — and therefore their
  API key — by typing their name.
- **Searches run as background jobs.** A multi-source search takes minutes and
  cannot be held open by an HTTP request. `POST /api/searches` returns a job id;
  the client polls status and pages results. Cancellation, expiry (410, "run it
  again") and per-user ownership are all enforced.
- **Summaries** with the pluggable LLM backend, under a per-user daily cap on
  summaries billed to the owner's key.
- **Bring-your-own-key**: a colleague can store their own DeepSeek or Anthropic
  key, encrypted at rest. Only its last four characters ever leave the server.
- **Per-user saved filters** in SQLite, seeded from `filters.json` on first
  sign-in, because a single shared file in a container is last-write-wins.
- `src/user_store.py` for users, their filters and their usage.

### Fixed
- The owner-key spend cap was a check-then-act race: the count was read at
  submission and written when the job finished, so a burst of requests all
  passed. A slot is now reserved atomically at admission.
- The user-supplied abstract was not budgeted before being sent to the model,
  so one crafted request could send an arbitrarily large prompt on the owner's
  key. Both prompt halves are budgeted and the request body has a ceiling.
- A segmentation fault on shutdown: a job worker mid-write while the database
  closed under it. Shutdown now drains with a bounded wait and the caller only
  closes shared resources when it succeeded. This would have hit on redeploys.

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
