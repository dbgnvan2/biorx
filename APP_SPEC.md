# BioRxiv Research Tool - App Specification

## 1. Overview

A desktop/web application that enables researchers to search the bioRxiv preprint server, download papers, and generate summaries using AI. The app provides an intuitive GUI with flexible search options and batch operations for research efficiency.

**Primary Goal:** Streamline literature research by combining search, discovery, and summarization in a single interface.

---

## 2. Core Features

### 2.1 Search & Discovery
- **Multi-criteria search** powered by bioRxiv API
- **Search filters:**
  - **Date range** (from/to dates)
  - **Number of recent posts** (e.g., "last 50 papers")
  - **Time window** (e.g., "last 7 days")
  - **Subject category** (biology categories available via API)
  - **Server type** (bioRxiv, medRxiv, etc.)
  - **Publisher prefix** (optional, for specific publishers)
  - **Funder** (filter by funding organization via ROR ID)

- **Results display:**
  - Title, authors, publication date, abstract
  - DOI link
  - Category/subject tags
  - Funding information
  - License type
  - Pagination/infinite scroll through results

### 2.2 Download & Access
- **Download PDFs** directly from bioRxiv
- **Batch download** multiple papers at once
- **Local storage** management (organize downloaded papers by search/category)
- **Link directly** to paper on bioRxiv (without download)

### 2.3 Summarization
- **AI-powered summarization** using Claude API
  - Summary length options (brief, detailed, key findings)
  - Extract key findings, methodology, conclusions
- **Batch summarization** of multiple papers
- **Save summaries** alongside papers
- **Export summaries** (as notes, markdown, PDF)

### 2.4 Organization & Workflow
- **Save/bookmark** papers for later
- **Collections** (e.g., "Review research", "Read later")
- **View history** of searched papers
- **Export** search results as CSV/JSON

---

## 3. User Interface Layout

### Main Window (Tabbed Interface)

#### Tab 1: Search & Browse
```
┌─────────────────────────────────────────────────────────┐
│ BioRxiv Research Tool  [Search] [Configure] [About]      │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  SEARCH PANEL (Left Sidebar)         │   RESULTS PANEL   │
│  ────────────────────────           │   ────────────────│
│  SAVED SEARCHES (from key_terms)     │                   │
│  ┌ Genetics & CRISPR                 │  ┌─────────────┐ │
│  │ ✓ CRISPR Gene Editing             │  │ Paper 1     │ │
│  │ ✓ CRISPR Delivery                 │  │ Title...    │ │
│  └ Infectious Disease                │  │ Authors...  │ │
│  │ ✓ COVID-19 Variants               │  │ 📥 ⭐ 📋   │ │
│  │   Influenza Research              │  ├─────────────┤ │
│  └ AI/ML in Biology                  │  │ Paper 2     │ │
│                                       │  │ Title...    │ │
│  [ Run Selected ] [ Run All Enabled ] │  │ Authors...  │ │
│                                       │  │ 📥 ⭐ 📋   │ │
│  MANUAL SEARCH                        │  └─────────────┘ │
│  Date Range:                          │                   │
│    From: [__________]                │  [⬅ Prev] [Next ➡] │
│    To:   [__________]                │                   │
│  Category: [Select ▼]                │                   │
│                                       │                   │
│  [ Search ] [ Clear ]                │                   │
│                                       │                   │
│  COLLECTIONS                         │  Status: Ready    │
│  📁 All Results                      │                   │
│  📁 Bookmarked                       │                   │
│  📁 Downloaded                       │                   │
│  📁 Summarized                       │                   │
│                                       │                   │
└─────────────────────────────────────────────────────────┘
```

#### Tab 2: Configure Search Terms
```
┌─────────────────────────────────────────────────────────┐
│ Configure Search Clusters                          [Save] │
├─────────────────────────────────────────────────────────┤
│                                                           │
│ SEARCH CLUSTERS                   │ PROFILES IN CLUSTER   │
│ ─────────────────────             │ ───────────────────  │
│ ☑ Genetics & CRISPR               │ ☑ CRISPR Gene Editing│
│ ☑ Infectious Disease              │ ☑ CRISPR Delivery    │
│ ☐ AI/ML in Biology                │ [ Add ] [ Edit ] [ - ]│
│ [+ New Cluster]                   │                       │
│                                    │ Details:              │
│                                    │ Keywords:             │
│                                    │ [CRISPR, gene editing]│
│                                    │                       │
│                                    │ Days back: [7]        │
│                                    │ Category: genetics   │
│                                    │                       │
│                                    │ [ Save ] [ Cancel ]  │
│                                    │                       │
└─────────────────────────────────────────────────────────┘
```

### Paper Detail View (Modal/Sidebar)
- Full abstract
- Complete author list with affiliations
- Funding details
- License information
- Action buttons:
  - Download PDF
  - Summarize
  - Bookmark
  - Share/Copy link
  - View on bioRxiv

### Summary View
- Original title & metadata
- Summary (user-selectable length)
- Key findings extracted
- Methodology overview
- Conclusions
- Export options

---

## 4. Technical Architecture

### Three-Component System

#### 1. **Desktop GUI** (PyQt6)
- Manual search, browse results, download PDFs
- View/manage summaries and bookmarks
- Configure key terms
- **Entry point:** `python gui.py`

#### 2. **Search Agent** (CLI)
- Reads `key_terms.json`
- Executes daily searches via bioRxiv API
- Downloads PDFs to `~/preprints/PDFs/`
- Stores metadata in SQLite
- **Entry point:** `python agents/search_agent.py`

#### 3. **Summarization Agent** (CLI)
- Queries SQLite for unsummarized papers
- Extracts text from PDFs
- Invokes Qwen 7B via Ollama
- Stores summaries in SQLite + text backup
- **Entry point:** `python agents/summarization_agent.py`

### Dependencies
- **PyQt6** - GUI framework
- **requests** - HTTP calls
- **pdfplumber** - PDF text extraction
- **sqlite3** - Built-in database
- **ollama** - Local model API client

### API Integrations
1. **bioRxiv API** - Search and metadata retrieval
2. **Ollama API** (localhost:11434) - Qwen inference
3. **PDF download** - Direct from bioRxiv servers

### Summarization Pipeline
- Use local Qwen model (or similar) to:
  - Extract key findings
  - Identify methodology
  - Summarize conclusions
  - No external API calls—fully offline-capable after model download

### Data Flows

#### Flow 1: GUI Ad-hoc Search
```
User clicks "Run Selected" or "Run All Enabled" in Search tab
    ↓
GUI reads key_terms.json
    ↓
For each enabled cluster/profile:
  - Execute bioRxiv API search
  - Display results in GUI (with pagination)
    ↓
User clicks "Download" or "Summarize" on paper
    ↓
[Download → ~/preprints/PDFs/] or [PDF → Extract → Qwen → Store in SQLite]
    ↓
Update GUI display (background thread)
```

#### Flow 2: GUI Manual Search
```
User enters custom search (date range, category) in GUI
    ↓
bioRxiv API request
    ↓
Display results with pagination
    ↓
User clicks "Download" or "Summarize"
    ↓
Execute action, update GUI
```

#### Flow 3: Automated (openclaw-scheduled)
```
openclaw triggers: python gui.py --run-search
    ↓
GUI (headless mode) reads key_terms.json
    ↓
Execute all enabled searches via search_agent
    ↓
Download PDFs to ~/preprints/PDFs/
    ↓
---
    ↓
openclaw triggers: python gui.py --run-summarize
    ↓
GUI (headless mode) executes summarization_agent
    ↓
Query SQLite for papers WHERE summarized=False
    ↓
For each paper: PDF → Extract Text → Qwen → Store in SQLite
    ↓
Update status (summarized=True)
```

---

## 5. Feature Priorities (MVP → Full)

### MVP (Minimum Viable Product)
**GUI (Search & Browse Tab):**
- [ ] Load and display key_terms.json search clusters
- [ ] "Run Selected" / "Run All Enabled" buttons to trigger searches from key_terms
- [ ] Manual search by date range & subject category
- [ ] Display results (title, authors, abstract, DOI)
- [ ] Download PDF to `~/preprints/`
- [ ] Summarize single paper (Qwen 7B via Ollama, background thread)
- [ ] View summaries in UI
- [ ] Search results pagination

**GUI (Configure Tab):**
- [ ] View, add, edit, delete search clusters
- [ ] Add/edit/delete profiles within clusters
- [ ] Enable/disable clusters and profiles
- [ ] Save changes back to key_terms.json

**Agents/CLI:**
- [ ] `search_agent.py`: Read key_terms.json, execute searches, store in SQLite
- [ ] `summarization_agent.py`: Process unsummarized papers, query Ollama, store results
- [ ] Ensure idempotency (no duplicate downloads/summaries)
- [ ] Support `--cluster "Cluster Name"` to run specific clusters

### Phase 2
- [ ] Bookmarking papers in GUI
- [ ] Collections/organization
- [ ] Export results (CSV/JSON)
- [ ] Search history in GUI
- [ ] UI improvements (dark mode, better layouts)

### Phase 3
- [ ] Advanced filters (publisher, funder)
- [ ] Settings/preferences panel
- [ ] Paper comparison tools
- [ ] Research note annotations

---

## 6. Technology Stack

### Python App (GUI + Agents)
**Core Dependencies:**
- **PyQt6** - Desktop GUI framework
- **requests** - HTTP calls to bioRxiv API
- **pdfplumber** - PDF text extraction
- **ollama** - Local Qwen model API client
- **sqlite3** - Built-in Python database

**Optional:**
- **python-dotenv** - Environment variables
- **click** - CLI argument parsing for agents

**Installation:**
```bash
pip install PyQt6 requests pdfplumber ollama click
```

**Local LLM Setup:**
1. Install [Ollama](https://ollama.ai/)
2. Pull Qwen 7B: `ollama pull qwen:7b`
3. Run as service: `ollama serve` (listens on localhost:11434)

### Project Structure
```
biorx/
├── gui.py              (PyQt6 desktop application + headless CLI modes)
├── agents/
│   ├── search_agent.py       (Core search logic, called by GUI or CLI)
│   └── summarization_agent.py (Core summarization logic, called by GUI or CLI)
├── src/
│   ├── biorxiv_api.py   (bioRxiv API wrapper)
│   ├── db.py            (SQLite database utilities)
│   ├── pdf_handler.py   (PDF text extraction)
│   └── llm.py           (Ollama/Qwen interface)
├── key_terms.json      (User configuration - editable from GUI)
└── ~/preprints/         (Data directory)
```

### GUI Usage Modes
```bash
# Interactive GUI (normal mode)
python gui.py

# Headless search via openclaw (or manual CLI)
python gui.py --run-search [--cluster "Cluster Name"]

# Headless summarization via openclaw
python gui.py --run-summarize [--max-count 10]

# Headless search + summarize
python gui.py --run-full-cycle
```

### openclaw Integration (Optional)
**Suggested schedule** (configure in openclaw):
```
Daily @ 8:00 AM  → python gui.py --run-search
Daily @ 9:00 AM  → python gui.py --run-summarize
```

Both modes are idempotent—safe to run multiple times without duplication.

---

## 7. Local LLM Summarization Details

### Qwen Model Setup
- **Model:** Qwen2 or Qwen2.5 (varies by model size/speed tradeoff)
- **Inference Engine:** Ollama (simple, local HTTP API)
- **Prompt Strategy:** Provide paper abstract + sections, request:
  - Key findings (2-3 bullet points)
  - Methodology summary
  - Conclusions/implications

### Summarization Workflow
```
1. User clicks "Summarize" on paper
2. Background thread spawned (UI remains responsive)
3. App downloads PDF to ~/preprints/PDFs/ (if not already downloaded)
4. Extract text from PDF (abstract + full text)
5. Invoke local Qwen 7B model via Ollama API (localhost:11434)
6. Extract structured data from response:
   - Key findings (bullet points)
   - Methodology summary
   - Conclusions/implications
7. Store in SQLite + text backup in ~/preprints/summaries/
8. Update UI when complete (show summary, update status)
```

### Qwen Prompt Template
```
You are a research paper summarization assistant.
Extract and summarize the following paper in this format:

ABSTRACT:
{abstract}

FULL TEXT:
{text}

---

Provide ONLY this structured output:

KEY FINDINGS:
- Finding 1
- Finding 2
- Finding 3

METHODOLOGY:
[One paragraph summary]

CONCLUSIONS:
[One paragraph summary]
```

### Benefits of Local LLM
✓ Fully offline after model download
✓ No API costs
✓ Data stays local (privacy)
✓ Faster iteration (no network latency)
✗ Slower than cloud APIs (depends on hardware)
✗ Requires VRAM for model (~8-16GB for decent Qwen variants)

---

## 8. Implementation Decisions (Finalized)

- **Model:** Qwen 7B (balance of quality and speed)
- **Summarization mode:** One paper at a time (sequential, not batch)
- **Threading:** Background threads for summarization (UI stays responsive)
- **Summary storage:** SQLite database + text file backups
- **Automation:** Integration planned with openclaw for scheduled summarization runs
- **PDF text extraction:** Abstract + full text (let model decide what's relevant)

---

## 9. File Organization & Configuration

### /preprints Directory Structure
```
~/preprints/
├── PDFs/
│   ├── 2025.03.001_title_slug.pdf
│   ├── 2025.03.002_title_slug.pdf
│   └── ...
├── summaries/  (text backup)
│   ├── 2025.03.001.txt
│   ├── 2025.03.002.txt
│   └── ...
├── biorxiv.db (SQLite database)
│   └── Contains: papers, summaries, bookmarks, search history
└── .gitkeep
```

### Key Terms Configuration

**File:** `key_terms.json` (organized by search categories)
```json
{
  "search_clusters": {
    "Genetics & CRISPR": {
      "enabled": true,
      "profiles": [
        {
          "name": "CRISPR Gene Editing",
          "category": "genetics",
          "keywords": ["CRISPR", "gene editing", "base editing"],
          "days_back": 7,
          "enabled": true
        },
        {
          "name": "CRISPR Delivery",
          "category": "genetics",
          "keywords": ["CRISPR delivery", "AAV", "nanoparticles"],
          "days_back": 7,
          "enabled": true
        }
      ]
    },
    "Infectious Disease": {
      "enabled": true,
      "profiles": [
        {
          "name": "COVID-19 Variants",
          "category": "virology",
          "keywords": ["COVID-19", "SARS-CoV-2", "variant"],
          "days_back": 3,
          "enabled": true
        },
        {
          "name": "Influenza Research",
          "category": "virology",
          "keywords": ["influenza", "H1N1", "pandemic"],
          "days_back": 7,
          "enabled": false
        }
      ]
    },
    "AI/ML in Biology": {
      "enabled": false,
      "profiles": [
        {
          "name": "Deep Learning for Protein Structure",
          "category": "bioinformatics",
          "keywords": ["deep learning", "protein structure", "AlphaFold"],
          "days_back": 14,
          "enabled": true
        }
      ]
    }
  },
  "settings": {
    "download": {
      "auto_download": true,
      "max_papers_per_run": 50
    },
    "summarization": {
      "auto_summarize": true,
      "max_summaries_per_run": 10
    }
  }
}
```

**Structure Notes:**
- **Search clusters:** Top-level groupings (e.g., "Genetics & CRISPR", "Infectious Disease")
- **Profiles:** Individual searches within a cluster
- `days_back`: How far back to search (prevents re-fetching old papers)
- `keywords`: Optional—if provided, filter results locally (client-side)
- `enabled`: Toggle clusters or individual profiles on/off

### Database Schema (SQLite)
```sql
papers
  - id (PRIMARY KEY)
  - doi
  - title
  - authors (JSON)
  - abstract
  - pub_date
  - category
  - url
  - downloaded (BOOLEAN)
  - pdf_path

summaries
  - id (PRIMARY KEY)
  - paper_id (FOREIGN KEY)
  - summary_text
  - key_findings (JSON)
  - methodology (TEXT)
  - conclusions (TEXT)
  - created_at (TIMESTAMP)
  - model_version (for tracking which Qwen version generated it)

bookmarks
  - id (PRIMARY KEY)
  - paper_id (FOREIGN KEY)
  - created_at (TIMESTAMP)

search_history
  - id (PRIMARY KEY)
  - query_params (JSON)
  - results_count
  - searched_at (TIMESTAMP)
```

**File naming convention:** Use DOI or bioRxiv ID + title slug for easy identification

---

## 10. Agent Idempotency & Deduplication

### Search Agent
- Query SQLite for `max(pub_date)` to avoid re-fetching old papers
- Check `papers.doi` before inserting (unique constraint)
- Only download PDFs if `downloaded = False`
- Safe to run multiple times without side effects

### Summarization Agent
- Query SQLite: `SELECT * FROM papers WHERE summarized = False`
- Check if `summaries.paper_id` exists before processing
- If summary exists, skip paper
- Safe to run multiple times without duplicating work

### Database Constraints
```sql
CREATE UNIQUE INDEX idx_papers_doi ON papers(doi);
CREATE UNIQUE INDEX idx_summaries_paper_id ON summaries(paper_id);
```

This ensures:
- No duplicate papers
- One summary per paper
- Agents can run repeatedly without issues

---

## 11. API Usage Notes

- Results paginate at **100 items per call**
- Use cursor parameter to navigate pages
- Date format: **YYYY-MM-DD**
- Supports both URL-encoded and underscore-separated category names
- Rate limits: typically generous for automated queries (check docs)

---

## Next Steps

1. Review this spec and provide feedback
2. Clarify technology stack preference
3. Define MVP scope (which features to build first)
4. Set up project repository
5. Begin frontend mockups & data flow design
