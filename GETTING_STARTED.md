# Getting Started with BioRx

BioRx searches nine publication sources (Europe PMC, PubMed, PsyArXiv, SocArXiv, arXiv, bioRxiv/medRxiv, and more), deduplicates and enriches the results, and can summarize papers using an LLM. You can use it as a **hosted web app** (no install), run it **locally as a web server**, or run the **desktop app**.

---

## Option A — Hosted web app (someone else runs the server)

If a colleague has deployed BioRx for you:

1. Open the URL they gave you.
2. Enter the shared access code.
3. Go to **Searches → New search**, enter your terms and date range, and run it.

That is all. Skip to [Your first search](#your-first-search) for tips on what to enter.

---

## Option B — Run the web app yourself (local)

### Prerequisites

- Python 3.11+
- An LLM API key (Anthropic or DeepSeek) **or** [Ollama](https://ollama.com/) running locally

### Steps

```bash
git clone https://github.com/dbgnvan2/biorx.git
cd biorx
pip install -r requirements-web.txt
```

Copy the example environment file and fill it in:

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

| Variable | What to set |
|---|---|
| `ACCESS_CODE` | Any passphrase — this is what you type to sign in |
| `SESSION_SECRET` | A random string: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEFAULT_LLM_PROVIDER` | `deepseek` (default), `anthropic`, or `ollama` — old name `LLM_PROVIDER` |
| `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY` | Your key for whichever provider you chose |
| `BIORX_CONTACT_EMAIL` | Your email (sent in API request headers for polite-pool access) |

Start the server:

```bash
SESSION_COOKIE_INSECURE=1 uvicorn web.app:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and enter your access code.

> `SESSION_COOKIE_INSECURE=1` is required over plain `http://`. Remove it if you add HTTPS.

---

## Option C — Deploy to Railway (share with colleagues)

Railway hosts the app in a container with persistent storage. The free tier is enough for light use; a small paid plan handles a team.

1. Fork or push this repository to your GitHub account.
2. Create a new Railway project and connect your repository.
3. **Add a volume mounted at `/data`** — without it, the database and downloaded files are lost on every redeploy.
4. Set these environment variables in Railway's dashboard (do not commit them to the repo):

   ```
   ACCESS_CODE=your-shared-passphrase
   SESSION_SECRET=<random string>
   KEY_ENC_SECRET=<random string — required for colleagues to save their own API keys>
   DEFAULT_LLM_PROVIDER=deepseek
   ANTHROPIC_API_KEY=sk-ant-...
   BIORX_CONTACT_EMAIL=you@example.com
   DATA_DIR=/data
   ```

5. Deploy. Then run through the post-deploy checklist:

   - [ ] `GET /healthz` returns `access_code_set: true` and `owner_key_set: true`
   - [ ] Wrong access code is refused; right one signs you in
   - [ ] A search returns results and the Stop button works
   - [ ] One summary completes
   - [ ] Redeploy, then confirm your filters and summaries survived (proves the volume is mounted)

Share the Railway URL and your `ACCESS_CODE` with colleagues.

---

## Option D — Desktop app (local, with Ollama)

The desktop app runs entirely offline after setup. It requires [Ollama](https://ollama.com/) for summarization.

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed and running

### Steps

```bash
git clone https://github.com/dbgnvan2/biorx.git
cd biorx
pip install -r requirements.txt
```

Summaries use DeepSeek by default (`llm_config.yaml`). Put the key in `.env`,
which the app reads at start-up (never in `llm_config.yaml` — it is committed):

```bash
echo 'DEEPSEEK_API_KEY=your-key' >> .env
```

For fully offline summaries instead, set `default_provider: ollama` and pull the
model it names:

```bash
ollama pull qwen3.5:4b
```

Start the app:

```bash
python gui.py
```

### Headless / cron mode

`agents/monitor.py` runs saved filters without the GUI, suitable for scheduling:

```bash
python agents/monitor.py --all
python agents/monitor.py --filter "Agent Simulation" --max 50
python agents/monitor.py --all --json out/results.json
```

Exit code 0 = clean run, 2 = one or more sources failed or a PDF download failed.

---

## Your first search

**In the web app:**

1. Go to **Searches → New search**.
2. Enter a topic (e.g. `CRISPR epigenetic inheritance`), set a date range, and choose which sources to include.
3. Click **Run**. Results appear as they arrive from each source — a multi-source search takes 30–90 seconds.
4. Click a paper to read its abstract or request a summary.

**In the desktop app:**

1. Open the **Search & Browse** tab.
2. Select a saved cluster or use the manual search form.
3. Click **Run Selected**.

---

## Configuring sources

Sources are configured in `sources_config.yaml`. The defaults (Europe PMC, PubMed, PsyArXiv, SocArXiv) cover most social-science and biomedical preprint searches. Enable `biorxiv_medrxiv` or `arxiv` if your topic warrants them.

Set your contact email in the same file (or via `BIORX_CONTACT_EMAIL`) — it goes in API request headers for polite-pool access and is required for Unpaywall open-access lookups:

```yaml
contact_email: you@example.com
```

---

## Choosing an LLM for summaries

| Provider | Cost | Notes |
|---|---|---|
| **Anthropic** (Claude Sonnet 5) | ~$0.003 per summary | Best quality; default when `DEFAULT_LLM_PROVIDER=anthropic` |
| **DeepSeek** (deepseek-chat) | ~$0.0003 per summary | Good quality, very low cost |
| **Ollama** (Qwen 7B, local) | Free | Requires Ollama running; not reachable from a cloud deployment |

In the web app, each user can paste their own Anthropic or DeepSeek key under **LLM settings** — summaries then bill to their key, not the owner's. The owner's key is the fallback and is capped at 25 summaries per user per day by default.

---

## Where data lives

| Location | Contents |
|---|---|
| `~/preprints/biorxiv.db` (local) or `/data/biorxiv.db` (Railway) | All paper metadata and summaries |
| `~/preprints/PDFs/` or `/data/PDFs/` | Downloaded PDF files |
| `filters.json` | Saved search filters |
| `sources_config.yaml` | Source enable/disable and contact email |

---

## Troubleshooting

**Search returns nothing**
- Check that `contact_email` is set in `sources_config.yaml` or `BIORX_CONTACT_EMAIL`. Several APIs require it.
- Widen the date range or simplify the query.

**"Source unavailable" in results**
- One or more APIs were unreachable during this search. The result set is partial; the page says which sources failed. Re-run to retry.

**Summaries fail or are empty**
- Run `GET /healthz` and confirm `owner_key_set: true` and the expected provider is shown.
- Check that the API key is valid and has credit.

**Ollama not found (desktop app)**
- Run `ollama serve` in a terminal, then restart the app.
- Confirm the model is downloaded: `ollama list` should show `qwen3.5:4b`
  (the model named in `llm_config.yaml`).
