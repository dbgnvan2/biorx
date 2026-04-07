# BioRxiv Research Tool (BioRx) - Gemini Context

This project is a multi-source research tool designed to search, download, and summarize scientific preprints and papers. It features a PyQt6 GUI for interactive use and CLI agents for automated workflows, utilizing a local LLM (Qwen 7B via Ollama) for summarization.

## 🏗️ Architecture & Core Components

### 1. **PyQt6 GUI (`gui.py`)**
- **Search & Browse Tab:** Interactive search with multi-source selection, pagination, and direct actions (Download, Summarize, Bookmark).
- **Configure Tab:** Manage search clusters and profiles stored in `key_terms.json`.
- **Reference Lists:** Manage collections of papers in named lists.
- **Headless Mode:** Support for `--run-search`, `--run-summarize`, and `--run-full-cycle` for automation (e.g., via openclaw).

### 2. **Search Orchestrator (`src/sources/orchestrator.py`)**
- Coordinates searches across multiple adapters:
  - **Europe PMC** (`europepmc.py`)
  - **PsyArXiv** (`psyarxiv.py`)
  - **bioRxiv / medRxiv** (`biorxiv_medrxiv.py`)
  - **CrossRef** (`crossref.py`) - for enrichment
  - **Unpaywall** (`unpaywall.py`) - for OA resolution
- Handles normalization to `CanonicalRecord`, deduplication (`dedup.py`), and ranking.

### 3. **Agents (`agents/`)**
- `search_agent.py`: Executes automated searches based on `key_terms.json`.
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

### Python & GUI
- **Python 3.9+** with type hints.
- **PyQt6** for GUI; use background threads (`threading.Thread`) for long-running operations (API, LLM) to keep UI responsive.
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
- `key_terms.json`: Search clusters/profiles.
- `sources_config.yaml`: Enabled/disabled search sources and API credentials.
- `requirements.txt`: Project dependencies.
