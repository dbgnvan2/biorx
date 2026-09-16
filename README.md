# BioRxiv Research Tool

**Purpose:** Desktop GUI app + headless agents that search nine publication sources,
deduplicate and enrich the results, download papers, and summarize them with a local
Qwen 7B model.

## Quick Start for Claude Code

1. **Read:** `APP_SPEC.md` for full specification
2. **Key files to create:**
   - `gui.py` - PyQt6 desktop application
   - `agents/search_agent.py` - Search logic
   - `agents/summarization_agent.py` - Summarization logic
   - `src/biorxiv_api.py` - API wrapper
   - `src/db.py` - SQLite utilities
   - `src/pdf_handler.py` - PDF text extraction
   - `src/llm.py` - Ollama/Qwen interface
   - `key_terms.json` - Configuration file (template)
   - `requirements.txt` - Dependencies

## Tech Stack
- **Language:** Python 3.9+
- **GUI:** PyQt6
- **Database:** SQLite3
- **LLM:** Qwen 7B (via Ollama)

## Sources

Searches run across every enabled source, are deduplicated (DOI, then title +
first author + year), enriched via Crossref/Unpaywall, and ranked by source trust.

| Source | Kind | Enabled by default |
|---|---|---|
| Europe PMC | peer-reviewed | yes, selected |
| PubMed | peer-reviewed | yes, selected |
| PsyArXiv | preprint | yes, selected |
| SocArXiv | preprint | yes, selected |
| bioRxiv / medRxiv | preprint | yes, not selected |
| arXiv | preprint (CS / LLM-agent / simulation) | yes, not selected |
| OpenAlex | index | no |
| Crossref, Unpaywall | enrichment only | yes |

Configure in `sources_config.yaml`. Saved searches live in `filters.json`.

## Headless CLI

`agents/monitor.py` runs saved filters without PyQt6, so cron can drive it. It
applies the same client-side filtering the GUI applies (`src/filtering.py`), so a
filter means the same thing on both surfaces.

```bash
python agents/monitor.py --filter "Agent Simulation" --dry-run --max 50
python agents/monitor.py --all --json out/results.json
```

Records are emitted as one JSON object per line on stdout; progress goes to stderr.

## MVP Scope
- GUI with Search & Browse + Configure tabs
- Load/edit search clusters from key_terms.json
- Run searches ad-hoc or on schedule
- Download PDFs to `/preprints/`
- Summarize papers with Qwen 7B (background threads)
- View/manage summaries in SQLite
- CLI headless modes for openclaw automation

## Key Design Decisions
- ✅ **Categorized searches:** Organize key_terms into clusters (e.g., "Genetics & CRISPR")
- ✅ **Local LLM:** Qwen 7B via Ollama (no cloud API, fully offline after model download)
- ✅ **Single papers:** Summarize one at a time, not in batches
- ✅ **Background threads:** Keep UI responsive during summarization
- ✅ **Idempotent agents:** Safe to run multiple times without duplicates
- ✅ **Dual mode:** Interactive GUI or headless CLI (for openclaw scheduling)

## Data Storage
```
~/preprints/
├── PDFs/               (Downloaded papers)
├── summaries/          (Text backups)
└── biorxiv.db          (SQLite: papers, summaries, bookmarks)
```

## In progress

A small private web app (FastAPI) so a few colleagues can run their own searches
and summaries from a browser, with a pluggable LLM backend (Ollama / DeepSeek /
Anthropic) and bring-your-own-key support. Plan and acceptance criteria:
`docs/implementation_plan_2026-09-15.md`.

---

See `APP_SPEC.md` for complete specification including database schema, API flows, UI mockups, and detailed feature list.
