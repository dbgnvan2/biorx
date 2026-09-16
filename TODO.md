# TODO

Deferred items, with the reason each was not fixed when found. Nothing here is a
blocker; each should land with a test.

## From the Hermes QA gate, 2026-09-15 (`docs/cycles/2026-09-15_chunk1-qa-gate.md`)

Verdict was APPROVED with these non-blocking findings. Carried rather than fixed
in-batch, because an APPROVED verdict only covers the code the gate read.

- **F3 (medium, P5/P2)** — arXiv is the only source that applies the `authors`
  filter at query time, and its `au:"…"` phrase clause is stricter than the
  client-side substring match, so an author-filtered search returns fewer results
  when arXiv is selected. Decide: drop the query-time author clause and let the
  client-side filter own author matching, or loosen the clause.
- **F1 (medium, P1/P5)** — the arXiv adapter retries 429 but raises immediately on
  a transient 5xx or a timeout, which the orchestrator treats as terminal for the
  whole run. Retry those with backoff before raising.
- **F2 (medium, P2)** — `monitor.py:download_pdf` logs failures at DEBUG and the
  caller discards the return value, so a requested PDF can go missing silently.
  Log at warning, count failures, print a `downloaded X / failed Y` summary.
- **F4 (low, P4/P6)** — the arXiv version is discarded and
  `CanonicalRecord.to_dict()` hardcodes `version: "1"`, so the
  "2+ (revised only)" filter can never match an arXiv paper.

## From the chunk-2 QA gate (`docs/cycles/2026-09-15_chunk2-qa-gate.md`)

APPROVED at fix-loop 2, with three low findings carried rather than fixed:

- `Database.release()` is wired into `SearchWorker` only. Safe today because
  every GUI worker gets a fresh QThread and the finalizer collects it, but the
  other workers (download, summarize, abstract fetch) should release explicitly
  too if they ever run on a pooled thread.
- `check_same_thread=False` is set on every connection so the finalizer can
  close a dead thread's handle. It also suppresses SQLite's own cross-thread
  guard globally, so a future thread-locality regression would fail silently
  rather than loudly.
- `_release_connection` catches only `sqlite3.Error`. At interpreter shutdown a
  different exception could escape and print "Exception ignored".

## Adjacent classes noted while fixing the chunk-2 gate

- **Hardcoded personal contact addresses remain in the retrieval layer**:
  `src/sources/config.py` (unpaywall_email, crossref_user_agent defaults) and
  `src/sources/arxiv.py` (the adapter User-Agent). `src/paper_meta.py` was moved
  to `BIORX_CONTACT_EMAIL`; the others are the same class and should follow, but
  they sit in files the web-app plan agreed not to modify. Note that arXiv wants
  a descriptive contact in its UA, so the replacement must keep a real address
  in deployment config rather than dropping to `example.com`.
- **`datetime.utcnow()` is deprecated** and used by every adapter
  (`europepmc.py`, `psyarxiv.py`, `socarxiv.py`, `biorxiv_medrxiv.py`,
  `arxiv.py`, `cache.py`). Fix as a class, not one at a time (P5).

## From the code review, 2026-09-15

- The CLI has **no failure signal**: the orchestrator swallows per-source errors
  and `monitor.py` exits 0 regardless, so a cron run where arXiv 429'd looks
  exactly like a quiet fortnight.
- **Wildcards are not handled for arXiv.** `build_psyarxiv_query` strips trailing
  `*`; `_group_to_arxiv` does not, and arXiv has no `*` operator.
- **`filters.json` names are not unique** (two entries are currently called
  "New Filter"), and `monitor.load_filters()` keys by name, so `--all` silently
  drops one.
- **`dict | None` in `agents/monitor.py`** breaks the Python 3.9 floor that
  `CLAUDE.md` and the arXiv spec claim to support.
- **`all:` is broader than Europe PMC's bare term** — it also matches authors,
  comments and journal-ref. Defensible, but currently undocumented.

## Pre-existing, found while reading

- `src/llm.py` — the summarization prompt f-string contains
  `{full_text[:3000]}  # Limit to first 3000 chars…`; the `#` is inside the
  string, so that comment is sent to the model on every call.
- `src/llm.py` — that 3000-character truncation is silent (P9 / L7: announce it).
- `src/llm.py` — `OLLAMA_MODEL = "qwen:7b"` is hardcoded in source (L1: model ids
  belong in config).
- `src/db.py:_run_migrations` — `except Exception: pass` around every
  `ALTER TABLE`, so a real migration failure is indistinguishable from "column
  already exists".
- `/opt/homebrew/bin/pytest` is Python 3.11 **without PyQt6**, so every GUI test
  is permanently skipped under the documented test command.
- `~/.claude/standards/llm-integration.md` L1 lists stale "current" Anthropic
  model ids. Lives in the separate `claude-standards` repo.
