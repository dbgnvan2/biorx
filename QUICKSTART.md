# Quick Start Guide

## Prerequisites

1. **Python 3.9+**
2. **Ollama** running with Qwen 7B model
   - Install: https://ollama.ai/
   - Pull model: `ollama pull qwen:7b`
   - Start service: `ollama serve` (runs on localhost:11434)

## Installation

```bash
cd /Users/davemini2/ProjectsLocal/biorx

# Install dependencies
pip install -r requirements.txt
```

## Running the App

### Interactive GUI
```bash
python gui.py
```

The GUI will open with two tabs:

**Tab 1: Search & Browse**
- Select saved search clusters from `key_terms.json`
- Click "Run Selected" or "Run All Enabled" to search
- Or perform manual searches by date range and category
- Results appear in table with pagination
- Download PDFs or summarize papers

**Tab 2: Configure**
- View and edit search clusters
- Add/remove/enable-disable clusters and profiles
- Changes saved to `key_terms.json`

### Command-Line (Agents)

**Search for papers:**
```bash
python agents/search_agent.py --all
python agents/search_agent.py --cluster "Genetics & CRISPR"
python agents/search_agent.py --dry-run  # Don't save to DB
```

**Summarize papers:**
```bash
python agents/summarization_agent.py  # Summarize up to 10 papers
python agents/summarization_agent.py --max-count 5
python agents/summarization_agent.py --paper-id 1  # Specific paper
python agents/summarization_agent.py --mock  # Test without Ollama
```

### Headless Mode (for openclaw)
```bash
python gui.py --run-search  # Run all enabled searches
python gui.py --run-search --cluster "Name"  # Run specific cluster
python gui.py --run-summarize --max-count 10  # Summarize papers
python gui.py --run-full-cycle  # Search + summarize
```

## Configuration

Edit `key_terms.json` to customize search clusters:

```json
{
  "search_clusters": {
    "Cluster Name": {
      "enabled": true,
      "profiles": [
        {
          "name": "Search Profile",
          "category": "genetics",
          "keywords": ["CRISPR", "gene editing"],
          "days_back": 7,
          "enabled": true
        }
      ]
    }
  }
}
```

## Data Storage

```
~/preprints/
├── PDFs/           (Downloaded papers)
├── summaries/      (Text backups of summaries)
└── biorxiv.db      (SQLite database with metadata)
```

## Troubleshooting

### "Ollama not available"
- Make sure Ollama is running: `ollama serve`
- Check model is installed: `ollama list` (should show qwen:7b)
- Verify it's on localhost:11434

### "PDF extraction failed"
- Make sure pdfplumber is installed: `pip install pdfplumber`
- Some PDFs may not be extractable

### "Database locked"
- Close the GUI and any other running agents
- Delete old `.db` lock files if they persist

## Next Steps

1. Configure your search clusters in `key_terms.json`
2. Run searches from the GUI or CLI
3. Review downloaded papers
4. Summarize papers with Qwen 7B
5. (Optional) Schedule with openclaw for daily runs

## Files Overview

- **gui.py** - Main PyQt6 application
- **agents/search_agent.py** - Search execution logic
- **agents/summarization_agent.py** - Summarization logic
- **src/biorxiv_api.py** - bioRxiv API wrapper
- **src/db.py** - SQLite database utilities
- **src/pdf_handler.py** - PDF download and text extraction
- **src/llm.py** - Ollama/Qwen interface
- **key_terms.json** - Search configuration
- **APP_SPEC.md** - Full specification
- **CLAUDE.md** - Development guidelines
