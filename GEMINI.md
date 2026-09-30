# BioRxiv Research Tool (BioRx) - Gemini Context

This project is a multi-source research tool designed to search, save, and summarize scientific preprints and papers. It is a small private web app (FastAPI + a plain JavaScript page, deployed on Railway) with CLI agents for automated workflows. Summaries use the LLM named in `llm_config.yaml` (DeepSeek by default; Anthropic or local Ollama optional). The PyQt6 desktop app (`gui.py`) was retired on 2026-09-30.

## 🏗️ Architecture & Core Components

### 1. **Web app (`web/`)**
- FastAPI routes for searches, filters, reference lists, summaries, reviews and sign-in (personal access code + PIN).
- Background jobs (`src/jobs.py`); the page is `web/static/`.

### 2. **Search Orchestrator (`src/sources/orchestrator.py`)**
- Coordinates searches across multiple adapters:
  - **Europe PMC** (`europepmc.py`)
  - **PsyArXiv** (`psyarxiv.py`)
  - **bioRxiv / medRxiv** (`biorxiv_medrxiv.py`)
  - **CrossRef** (`crossref.py`) - for enrichment
  - **Unpaywall** (`unpaywall.py`) - for OA resolution
- Handles normalization to `CanonicalRecord`, deduplication (`dedup.py`), and ranking.

### 3. **Agents (`agents/`)**
- `monitor.py`: Runs saved filters from `filters.json` headless.
- `summarization_agent.py`: Processes downloaded PDFs using local LLM.

### 4. **Core Utilities (`src/`)**
- `db.py`: SQLite3 management (Papers, Summaries, Bookmarks, Search History, Reference Lists).
- `llm.py`: Ollama/Qwen 7B interface (localhost:11434).
- `pdf_handler.py`: PDF downloading and text extraction (using `pdfplumber`).
- `sources/config.py`: Manages `sources_config.yaml` for feature flagging.

## 💾 Data Storage (`~/preprints/`)
- `biorxiv.db`: SQLite database.
- `PDFs/`: Downloaded preprint PDFs.
- `summaries/`: Text backups of generated summaries.

## 🛠️ Engineering Standards

### Python & web
- **Python 3.12** with type hints.
- Long-running work (searches, LLM calls) runs as background jobs so requests return at once.
- **Error Handling:** Graceful handling of API failures, rate limits, and PDF extraction errors.

### Database
- **SQLite3** with `check_same_thread=False` for multi-threaded access (writes are serial).
- **Idempotency:** Unique constraints on DOI and `paper_id` to prevent duplicates.
- **Migrations:** Additive migrations run on every startup in `Database._run_migrations`.

### LLM Summarization
- **Model:** Qwen 7B via Ollama.
- **Strategy:** Abstract + first 3000 chars of full text.
- **Output:** Structured into Key Findings, Methodology, and Conclusions.

## 🧪 Testing & Validation
- **Test Runner:** `pytest`
- **Key Tests:**
  - `tests/test_orchestrator.py`: Source routing and enrichment.
  - `tests/test_dedup.py`: Canonical record merging.
  - `tests/test_query_builder.py`: Lucene/Source-specific query generation.
  - `tests/test_adapters.py`: Normalization logic.

## 📝 Logging & Diagnostics
- **Log File:** `biorx.log` in project root.
- **Diagnosis:** Always check `tail -n 100 biorx.log` first for errors in adapters, database, or LLM calls.

## ⚙️ Configuration
- `filters.json`: Your saved filters (local, not in git); `filters.seed.json`: examples new web accounts start with.
- `sources_config.yaml`: Enabled/disabled search sources and API credentials.
- `requirements.txt`: Project dependencies.
