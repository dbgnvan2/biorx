# BioRxiv Research Tool - Claude Code Instructions

## Global standards

Read the relevant file from `~/.claude/standards/` before starting work:

| Standard | When |
|---|---|
| `learnings.md` | P1 (transient API failures), P2 (silent drop), P5 (harden all sibling calls) |
| `external-api.md` | Any bioRxiv API call — timeouts, `.json()` guarding, retry logic |
| `llm-integration.md` | Any Ollama/Qwen integration — output validation, token budgets, model config |
| `security.md` | SQLite parameterised queries (already used — keep it), no secrets in source |
| `file-maintainability.md` | Any new module or significant refactor |
| `ui-regression.md` | Any change to the web page (`web/static/`) |



## Project Overview
A small private web app (FastAPI + a plain JavaScript page) and command-line agents for
searching nine publication sources, saving reference lists, and summarizing papers with
the LLM named in `llm_config.yaml` (DeepSeek by default; Anthropic or local Ollama
`qwen3.5:4b` optional). Deployed on Railway.

The PyQt6 desktop app (`gui.py`) was retired on 2026-09-30 (decision D1 in
`docs/implementation_plan_2026-09-29_next.md`). Do not bring it back or add
desktop-only code; its old `reference_lists` tables are left in the database, unread.

**Data storage:** `DATA_DIR` (default `~/preprints/`): SQLite database, PDFs, summaries.

---

## Architecture & Scope

### Components
1. **Shared code** (`src/`) - sources and search (`src/sources/`), filtering, database,
   summaries, full-text lookup, accounts and access codes
2. **Web app** (`web/`) - FastAPI routes, background jobs, the page in `web/static/`
3. **Agents** (`agents/`) - `monitor.py` runs saved filters (cron); `summarization_agent.py`
   summarizes stored papers

### Key Design Decisions
- **Sign-in:** personal access code + PIN only (`access_codes.yaml`); the old shared
  `ACCESS_CODE` + name sign-in is gone
- **Background jobs:** `src/jobs.py`; searches and model calls in separate lanes
- **Idempotent agents:** Safe to run multiple times; check SQLite before inserting
- **Saved filters:** per user in the web app, seeded from `filters.seed.json`; `filters.json` is local state for `monitor.py` (not in git); facet options in `filter_vocabulary.yaml`
- **LLM:** DeepSeek by default (`DEEPSEEK_API_KEY` in `.env`); Ollama (localhost:11434, `qwen3.5:4b`) for offline use — set in `llm_config.yaml`
- **External APIs:** publication sources, Crossref/Unpaywall enrichment, and the configured LLM provider

---

## Code Style & Practices

### Python
- Python 3.12 (the only version CI and the Docker image run; review D4)
- Type hints where helpful (function signatures)
- Docstrings for modules and classes
- Error handling at system boundaries (API calls, file I/O)
- Use built-in modules (sqlite3, json, os, pathlib) before third-party

### Database
- SQLite3 (`biorxiv.db` in `DATA_DIR`)
- UNIQUE constraints on DOI and paper_id (prevent duplicates)
- Use parameterized queries (? placeholders) for safety
- Keep schema minimal; avoid over-normalization

### Web page
- Never `innerHTML` with data (a test checks); build with createElement/textContent
- Buttons that start background work are disabled before the request leaves

### API Calls
- Wrap source requests with timeout + retry + backoff
- Log errors but don't crash the app
- Respect rate limits

---

## Log File

The app writes logs to **`biorx.log`** in the project root (also mirrored to stderr).

**Always check the log proactively** before asking the user to describe an error:
```bash
tail -100 /Users/davemini2/ProjectsLocal/biorx/biorx.log
```
Look for `ERROR` and `WARNING` lines. Common sources:
- `src.sources.europepmc` — API errors, JATS parse failures
- `src.sources.unpaywall` — OA lookup failures (422 = not indexed, expected)
- `src.sources.orchestrator` — search routing / enrichment errors
- `src.db` — SQLite schema or insert errors

When the user reports unexpected behaviour, read the log tail **first** as part of diagnosis.

---

## Testing & Iteration

Run the test suite with the project venv:
```bash
venv/bin/python -m pytest tests/ -v
```
CI runs the same suite on a blank machine (`requirements-web.txt` + `requirements-test.txt`).

Test files and what they cover:
- `tests/test_query_builder.py` — Lucene query generation, species clauses, date ranges
- `tests/test_dedup.py` — deduplication logic and field merge rules
- `tests/test_adapters.py` — EuropePMC / PsyArXiv / Crossref adapter normalisation
- `tests/test_unpaywall.py` — Unpaywall HTTP status handling and enrich() behaviour
- `tests/test_orchestrator.py` — source routing, dedup across sources, enrichment calls
- `tests/web/` — the web app: routes, sign-in, jobs, and the page (node-run checks)

**Run tests after every non-trivial code change.** If a test fails, fix it before moving on.

---

## Git & Commits

- Commit as you complete logical units (e.g., "Add biorxiv_api wrapper")
- Include what and why in commit messages
- No need to push; this is local development

---

## Dependencies

`requirements-web.txt` (the web app; the Docker image installs its hashed lock) and
`requirements.txt` (extras for the command-line tools). Key ones: **FastAPI**,
**requests**, **pdfplumber**, **PyYAML**.
