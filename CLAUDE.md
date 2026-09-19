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
| `ui-regression.md` | Any change to PyQt6 screens or controls |



## Project Overview
Desktop GUI application (PyQt6) + CLI agents for searching bioRxiv preprints, downloading papers, and summarizing them with the LLM named in `llm_config.yaml` (DeepSeek by default; Anthropic or local Ollama `qwen3.5:4b` optional).

**Data storage:** `/preprints/` directory with SQLite database, PDFs, and summaries.

---

## Architecture & Scope

### Components (in build order)
1. **Core utilities** (`src/`) - API wrapper, database, PDF handler, LLM interface
2. **Agents** (`agents/`) - Search and summarization (callable from GUI or CLI)
3. **GUI** (`gui.py`) - PyQt6 desktop app with Search & Configure tabs

### Key Design Decisions
- **Single papers at a time:** Summarize one paper per user action (not batches)
- **Background threading:** Keep UI responsive during long operations
- **Idempotent agents:** Safe to run multiple times; check SQLite before inserting
- **Categorized searches:** `key_terms.json` organized by search clusters
- **LLM:** DeepSeek by default (`DEEPSEEK_API_KEY` in `.env`); Ollama (localhost:11434, `qwen3.5:4b`) for offline use — set in `llm_config.yaml`
- **External APIs:** publication sources, Crossref/Unpaywall enrichment, and the configured LLM provider

---

## Code Style & Practices

### Python
- Python 3.9+ syntax
- Type hints where helpful (function signatures)
- Docstrings for modules and classes
- Error handling at system boundaries (API calls, file I/O)
- Use built-in modules (sqlite3, json, os, pathlib) before third-party

### Database
- SQLite3 (biorxiv.db in ~/preprints/)
- UNIQUE constraints on DOI and paper_id (prevent duplicates)
- Use parameterized queries (? placeholders) for safety
- Keep schema minimal; avoid over-normalization

### Threading
- Use `threading.Thread` for background tasks (summarization)
- Update UI via signals/slots or post callbacks to main thread
- No blocking operations in GUI thread

### API Calls
- Wrap bioRxiv requests with error handling
- Log errors but don't crash app
- Respect rate limits (typically generous; check bioRxiv docs)

### File Organization
```
biorx/
├── gui.py                    (Main PyQt6 app)
├── agents/
│   ├── search_agent.py       (Search logic, callable from GUI or CLI)
│   └── summarization_agent.py (Summarization logic)
├── src/
│   ├── biorxiv_api.py        (bioRxiv API wrapper)
│   ├── db.py                 (SQLite helpers)
│   ├── pdf_handler.py        (PDF text extraction)
│   ├── llm.py                (Ollama/Qwen interface)
│   └── __init__.py           (empty, makes src a package)
├── key_terms.json            (User config, editable from GUI)
├── requirements.txt
├── CLAUDE.md                 (this file)
├── README.md
├── APP_SPEC.md              (full specification)
└── /preprints/              (data directory, created at runtime)
```

---

## MVP Features (Must Have)

### GUI - Search & Browse Tab
- [ ] Load key_terms.json search clusters
- [ ] Display enabled/disabled clusters as checkboxes
- [ ] "Run Selected" button - execute checked searches
- [ ] "Run All Enabled" button - run all enabled clusters
- [ ] Manual search form (date range, category)
- [ ] Results list with pagination
- [ ] Per-paper buttons: Download, Summarize, Bookmark
- [ ] Status updates (searching..., downloading..., summarizing...)

### GUI - Configure Tab
- [ ] Tree/list view of clusters and profiles
- [ ] Add/edit/delete cluster
- [ ] Add/edit/delete profile within cluster
- [ ] Toggle cluster/profile enabled/disabled
- [ ] Save button to write key_terms.json

### Agents
- [ ] search_agent.py: Read key_terms, execute searches, store in SQLite
- [ ] summarization_agent.py: Find unsummarized papers, run the configured LLM, store results
- [ ] Both callable from GUI or CLI (python gui.py --run-search, --run-summarize)

### Core Utilities
- [ ] biorxiv_api.py: Search by date range, category, keywords
- [ ] db.py: Papers table, summaries table, bookmarks, search history
- [ ] pdf_handler.py: Download PDF, extract text
- [ ] llm.py: Query Ollama (localhost:11434), parse Qwen responses

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

Run the test suite with the project venv, which has PyQt6:
```bash
venv/bin/python -m pytest tests/ -v
```
Another Python without PyQt6 (e.g. `/opt/homebrew/bin/pytest`) skips every desktop
GUI test; the run's summary then says how many were skipped and why. CI runs
without PyQt6 on purpose (`requirements-web.txt`), so GUI tests run only here.

Test files and what they cover:
- `tests/test_query_builder.py` — Lucene query generation, species clauses, date ranges
- `tests/test_dedup.py` — deduplication logic and field merge rules
- `tests/test_adapters.py` — EuropePMC / PsyArXiv / Crossref adapter normalisation
- `tests/test_unpaywall.py` — Unpaywall HTTP status handling and enrich() behaviour
- `tests/test_orchestrator.py` — source routing, dedup across sources, enrichment calls
- `tests/test_source_picker.py` — source picker model logic

**Run tests after every non-trivial code change.** If a test fails, fix it before moving on.

---

## Git & Commits

- Commit as you complete logical units (e.g., "Add biorxiv_api wrapper", "Add PyQt6 GUI skeleton")
- Include what and why in commit messages
- No need to push; this is local development

---

## Dependencies

See `requirements.txt`. Key ones:
- **PyQt6** - GUI
- **requests** - HTTP
- **pdfplumber** - PDF extraction
- **ollama** - Ollama API client
- **click** - CLI parsing (optional, can use sys.argv instead)

---

## Next Steps

1. Create directory structure
2. Implement core utilities (src/)
3. Implement agents
4. Implement PyQt6 GUI
5. Test end-to-end

Start with `src/biorxiv_api.py` (simplest, no external dependencies besides requests).

