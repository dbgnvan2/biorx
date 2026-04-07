# BioRxiv Research Tool

**Purpose:** Desktop GUI app + automated agents to search bioRxiv preprint server, download papers, and summarize them using a local Qwen 7B model.

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
- **API:** bioRxiv REST API

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

## Next Steps
1. Set up project structure
2. Build core utilities (API, DB, PDF handler, LLM interface)
3. Build agents (search, summarization)
4. Build PyQt6 GUI
5. Test with local Ollama + Qwen 7B

---

See `APP_SPEC.md` for complete specification including database schema, API flows, UI mockups, and detailed feature list.
