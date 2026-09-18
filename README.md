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

## Web app

A small private FastAPI app so a few colleagues can run their own searches and
summaries from a browser, without installing anything. It wraps the same
retrieval pipeline the desktop app uses — the orchestrator, dedup, query builder
and adapters are unchanged.

### Run it locally

```bash
pip install -r requirements-web.txt
cp .env.example .env          # then fill in ACCESS_CODE at minimum
set -a && source .env && set +a
SESSION_COOKIE_INSECURE=1 uvicorn web.app:app --reload --port 8000
```

Open http://127.0.0.1:8000 and enter the access code. `SESSION_COOKIE_INSECURE=1`
is needed only over plain HTTP; never set it in a deployment.

`GET /healthz` reports the effective configuration — which provider and model
are in use, whether an owner key is set, whether personal keys can be stored —
without ever reporting a secret. Check it first when something looks wrong.

### How access works

Two steps. The shared `ACCESS_CODE` (in `.env`) opens the door; give it to the
people you want to let in. Each person then signs in with **their own name and
PIN**: the same name and PIN always bring back the same account — its filters,
Saved References and API key — on any browser. Identity in the cookie is still a
server-issued opaque id; a name selects an account only together with its PIN.

- **Create account** shows a **recovery code** once. "Forgot PIN?" takes the
  name, that code and a new PIN, and issues a new code.
- PINs and recovery codes are stored as scrypt hashes. After
  `LOGIN_MAX_FAILURES` wrong attempts (default 5) an account is locked for
  `LOGIN_LOCK_MINUTES` (default 15) — the access code is shared, so this is what
  stops someone who has it from guessing a colleague's PIN.
- Accounts made before names and PINs existed keep working from the browser
  that made them; Settings → Your account lets them choose a name and PIN.
- To combine two accounts: `python -m src.accounts list --db PATH`, then
  `python -m src.accounts merge --db PATH --from ID --into ID`. Nothing is
  deleted; the old account's cookie leads to the merged one.

Rotate `ACCESS_CODE` when someone leaves: it stops them reaching the sign-in
page. Their account and data remain.

### LLM backends and keys

Three backends, configured in `llm_config.yaml`: local **Ollama** for
development, **DeepSeek** over its OpenAI-compatible API, and **Anthropic** over
the Messages API. `LLM_PROVIDER` picks the default.

Each summary resolves a credential in this order:

1. the requesting user's own key, if they saved one;
2. the server owner's key from the environment;
3. otherwise an error telling them to add a key.

A colleague can paste their own DeepSeek or Anthropic key in the **Settings** tab.
It is encrypted with Fernet before storage and only its last four characters are
ever shown. This requires `KEY_ENC_SECRET`; without it the app still runs on the
owner key and the UI says plainly that personal keys cannot be stored. The
secret is never generated automatically — one written next to the data it
protects is not protection.

**Summaries billed to the owner's key are capped** at 25 per user per day
(`SUMMARY_DAILY_CAP_PER_USER`). Users on their own key are not capped. The
access code is shared, so this is the ceiling on what a leaked code can spend.

### Searches are jobs

A multi-source search takes minutes, so `POST /api/searches` returns a job id
and the page polls it. Results page in as they arrive, a search can be stopped,
and a job that has aged out says so ("run it again") rather than vanishing. If a
source was unreachable the page says which, so an empty result is never mistaken
for a quiet week.

### Deploy to Railway

The repository has a `Dockerfile` and `railway.json`. The image installs
`requirements-web.txt` only — never `requirements.txt`, which would pull the
desktop GUI into a headless container.

1. Create a Railway project from this repository; it picks up `railway.json`.
2. **Attach a volume mounted at `/data`.** Without it the database and any
   downloaded PDFs are lost on every redeploy.
3. Set the variables from `.env.example`. At minimum: `ACCESS_CODE`,
   `SESSION_SECRET`, `KEY_ENC_SECRET`, `LLM_PROVIDER`, and the matching
   provider key.
4. Deploy, then walk the checklist below.

#### Deploy checklist (manual — these cannot be tested in CI)

- [ ] `GET /healthz` returns `access_code_set: true`, the expected `provider`
      and `model`, and `owner_key_set: true`.
- [ ] The wrong access code is refused; the right one signs you in.
- [ ] A saved search returns results, and "Stop" stops it.
- [ ] One summary completes, and the model shown matches what you configured.
- [ ] Saving a personal key shows only its last four characters.
- [ ] Redeploy, then confirm your filters and summaries are still there — this
      is what proves the volume is mounted.
- [ ] `docker build .` succeeds. The image is not built in CI, and was not built
      on the development machine (no Docker daemon running), so the first build
      is the first real test of it.

Live calls to Anthropic and DeepSeek are integration-only: every test in the
suite mocks them. Run one real summary per provider after a deploy and note the
date and model — that is the only thing that exercises the real API.

### What is deliberately not there

No accounts, SSO, or per-user permissions. No editing `sources_config.yaml` from
the browser. No scheduled searches — `agents/monitor.py` still owns that. The
web app offers what the desktop app offers, and nothing more.

---

See `APP_SPEC.md` for complete specification including database schema, API flows, UI mockups, and detailed feature list.
