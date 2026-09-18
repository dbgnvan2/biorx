# Changelog

## 2026-09-17 — web app review fixes (before first push of the parity work)

Found by learning-qa, /code-review and /security-review over the unpushed range.

### Security
- **PDF proxy SSRF closed** (`src/safe_fetch.py`). The proxy checked only the
  first URL and then followed redirects, missed link-local (cloud metadata) and
  CGNAT ranges, and treated an unresolvable name as safe. Now every hop must be
  https and resolve only to public addresses, the connection is pinned to the
  checked address with TLS verified against the hostname, the body is size-
  capped while streaming, and it must actually be a PDF.
- **Removed `POST /api/references/{id}/items`**, which stored a client-supplied
  paper (and its URL) for the proxy to fetch. Papers enter lists through
  save-as-list only.
- **Summaries fetch through the same guard.** `POST /api/summaries` downloaded
  the client's `pdf_url` and scraped its abstract URLs with plain `requests`,
  so an internal page could be read back through a summary, and the PDF was
  cached under the client's DOI/title where later summaries of the real paper
  would read it. Both now go through `src/safe_fetch.py`, into a temp file.
- DNS failures are a retryable 502, not a 403; a whole download has a time
  limit that holds even against a server trickling bytes; the PDF signature
  may follow leading bytes.
- A summary made from the abstract alone now says so, and why ("from the
  abstract only (full text not used: the link leads to a web page…)"). An
  `http://` PDF link is tried as `https://`.

### Fixed
- **Discover Terms** searched on single letters of the description, never showed
  its results, kept a daily-cap slot when it failed, and reported unparsable
  replies as "no terms". Settings and stop words are now in `llm_config.yaml`.
- **Filters saved from the web** lost their keywords (so matched everything),
  crashed every search (institution saved as a list), and ignored date ranges.
  Filters already stored in that shape are converted on the server wherever
  they are read or run (`src/filtering.normalise_filter`).
- **Reference lists:** deleting a list now deletes its items; a duplicate name
  is a 409, not a 500; save-as-list waits for the search to finish and reports
  papers it could not store; CSV exports the right bioRxiv version.
- Bulk PDF download and remove report what failed.
- `/api/me` reports the model that will actually run.
- A Discover run that finds no papers gives its daily-cap slot back.
- Saving or removing an API key reports a server failure instead of claiming
  success (a key could stay stored and billed after "Key removed.").
- Filter Test says when a source could not be reached, as the Search tab does.
- The PDF proxy tries an `http://` link as `https://`, like the summary path.

## 2026-09-17 — web Settings tab is per user

### Changed
- **The web app no longer edits the server's config files.** The Settings tab
  added in the parity commit let any signed-in user read and overwrite
  `sources_config.yaml` and `llm_config.yaml`, changing the app for everyone.
  `web/routes_settings.py` is removed; those files are owner-only.
- **Settings tab now holds each user's own settings:** the LLM panel (moved from
  the header) and new **default sources**, saved in the browser, which set the
  sources ticked in the Search tab and in new filters.
- The contact email moves out of the tracked `sources_config.yaml`; set
  `BIORX_CONTACT_EMAIL` in the environment.

### Fixed
- Opening a saved filter in the web app showed blank fields, and saving it then
  erased its keywords, dates and sources. The client read `f.filter`, a key the
  API never returns.
- Source checkboxes stacked above their labels, one per row.

## 2026-09-16 — polite User-Agent for all API calls (batch-H)

### Added / Fixed
- **Every HTTP request to a polite-pool API now sends a correct `biorx/1.0`
  User-Agent** (and `biorx/1.0 (mailto:EMAIL)` when a contact email is set).
  This applies to all eight consumers: EuropePMC, PubMed, PsyArXiv, SocArXiv,
  bioRxiv/medRxiv, Crossref, arXiv, and PDF downloads (both `pdf_handler.py`
  and `monitor.py`). Previously six adapters sent `ResearchTool/1.0` or no UA,
  and PDF downloads sent no UA at all.
- **Contact email** is read from `BIORX_CONTACT_EMAIL` env or `contact_email`
  in `sources_config.yaml`; no personal address is embedded in source code.
- **Startup warning** when no contact email is configured. The warning is:
  - Logged at startup for both GUI and CLI.
  - Shown in the GUI status bar (all warnings joined, never overwriting).
  - Returned by `/healthz` in the web app as `startup_warnings[]`.
  - Displayed by the web UI on boot via `startup_warnings.join(" | ")`.
- `PDFHandler()` with no `output_dir` argument now resolves via `DATA_DIR`
  environment variable instead of crashing. Default remains `~/preprints/PDFs`.

### Tests
- `test_h_environment.py`: 16 new tests covering all eight consumers, the
  startup-warning flow, pdf_handler default constructor, and monitor UA.
- `test_app.py`: `test_h_app_js_reads_healthz_startup_warnings_on_boot`
  asserts the render call (`startup_warnings.join`) survives comment-stripping
  (P27 mutation guard).
- `test_frontend_wiring.py`: `/healthz` added to the exact endpoint set.
- Retrieval-layer BASELINE advanced four times to `a55b83e`; test gates each
  advance against the drift guard.

## 2026-09-16 — recovering a missing abstract (N2)

### Fixed
- **A summary no longer fails just because the search record lacked an
  abstract.** Before giving up, the web app now looks for one — Europe PMC by
  DOI, PMC full text, Crossref, OpenAlex, then the paper's own pages — and
  stores what it finds with the paper. The paper that exposed this, an open-
  access article on PMC with no DOI, now recovers its full abstract.
- The desktop app's abstract lookup had the same gaps: it skipped PMC for any
  paper without a DOI, and reported "not available" as though it were the
  abstract. Both apps now share one lookup.
- A correction, erratum or retraction notice with no text is refused with a
  message saying so. The list of notice titles is in `llm_config.yaml`.
- Every source is held to a minimum abstract length, so a short error string
  or page tagline is never accepted as an abstract.

### Tests
- The whole suite now fails any test that tries to reach the internet, even if
  the code under test catches the error. It found one test that had been
  making live calls and failing intermittently.

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
