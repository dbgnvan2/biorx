# TODO

> Items from 2026-09-15 are being reconciled and worked through in
> `docs/implementation_plan_2026-09-16_backlog.md`; that plan is the current list.

## From the N2 gate (`docs/cycles/2026-09-16_n2-qa-gate.md`) — APPROVED at fix-loop 1

- **Network guard limits.** It cannot see network use from a subprocess (a
  fresh interpreter), from code that captured socket functions at import time,
  or from a library with its own resolver (dnspython, aiohttp). None of those is
  reachable in this repo today — its only network path is requests/urllib3 — but
  say so in the guard's docstring so nobody over-trusts it.
- **`_OUR_BUGS` is broader than its name**: `ImportError` can also mean an
  optional dependency is missing rather than a defect in this code.
- **Adjacent to N2**: `pdf_url()` falls back to `best_oa_url`, which for PMC is
  an HTML page, so a summary job first downloads a web page as though it were a
  PDF before recovery runs. Wasted work, not a wrong result.

## From the N1 gate (`docs/cycles/2026-09-16_n1-qa-gate.md`) — APPROVED

- **F1 (low)** — the N1 rebuild recreates `papers` from its CREATE statement, so
  any trigger, view, or explicit index on `papers` would be silently dropped.
  None exists in this repo (the canonical_id index is created after the
  rebuild), so no real database can hit it. If one is ever added, make the
  rebuild refuse, or recreate them.
- **Adjacent to N1** — `reference_list_items` deduplicates on `(list_id, doi)`;
  NULLs are distinct, so a DOI-less paper can be added to the same reference
  list more than once. Key it on `canonical_id` instead.

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

## From the chunk-5 QA gate (`docs/cycles/2026-09-15_chunk5-qa-gate.md`)

Rejected three times before approval — every rejection a real defect in
`docker-entrypoint.sh`, and every one invisible to the suite until the tests
were rewritten to run the script instead of grepping it. Carried findings:

- **F8 (low)** — `fatal()`'s second line always says "mount the volume writable
  by biorx", but the non-root branch fails under whatever uid the platform
  enforced. The first line names the real uid, so the hint merely misleads.
- **F6 (low)** — the "derive env vars from the code" test misses variables read
  through indirection (`KEY_ENC_SECRET`, the provider keys, the model names), so
  its guarantee rests partly on a hand-kept list.
- **No Content-Security-Policy header.** The client sets text rather than
  markup and checks URL schemes, but a CSP would be defence in depth.
- **Unbounded job and user creation**: anyone with the access code can create
  users and queue jobs without limit. The spend cap bounds money, not memory.

### The container image has not been built
Docker's daemon was not running on the development machine, so `docker build`
was never executed. Everything about the image is verified structurally or by
running `docker-entrypoint.sh` directly under `sh`/`dash`. The first real build
is the deployer's.

## From the chunk-4 QA gate (`docs/cycles/2026-09-15_chunk4-qa-gate.md`)

REJECTED, then APPROVED at fix-loop 1. The two blocking findings are fixed; four
low findings are carried:

- Job and user creation are unbounded: anyone with the access code can create
  users and queue jobs without limit. The spend cap bounds money, not memory.
- The shared summary endpoint returns `created_by_user_id`, which tells one
  colleague who ran a summary. Fine among colleagues; note it before the
  audience widens.
- The route-enumeration auth test asserts `checked >= 10`, a floor rather than
  an exact count (P29), and will need attention when the SPA adds routes.
- The constant-time access-code comparison is asserted by inspecting the
  function's bytecode names rather than by behaviour.
- A worker blocked with no timeout would delay interpreter exit by up to ~150s
  (bounded by the provider and PDF timeouts). A delay, not a hang.

## From the chunk-3 QA gate (`docs/cycles/2026-09-15_chunk3-qa-gate.md`)

APPROVED with five findings, all latent because nothing consumes these modules
yet. The first two must be closed in the web-routes phase, not after it.

- **MEDIUM — `JobRegistry.get()` collapses three different answers into `None`**:
  unknown id, expired job, and another user's job are indistinguishable, and the
  docstring promises an `expired` status that does not exist. A user whose job
  aged out should be told to re-run, not shown "not found".
- **MEDIUM — the job ownership check is opt-in** (`owner=None` default). Any
  future caller that forgets to pass `owner` gets unrestricted read and cancel.
  Make owner required at the boundary the routes use.
- **LOW — two failure contracts meet at the resolver**: the hosted clients raise
  typed errors, `OllamaClient` returns `None`. The route that calls
  `ResolvedLLM.client.summarize_paper()` has to handle both (P22).
- **LOW — a vacuous assertion** in `tests/web/test_crypto.py`
  (`assert monkeypatch.delenv(...) is None` is always true). A test line that
  cannot fail (P27).
- **LOW — `mask()` / `last4()` have no caller yet**; expected at this phase, but
  they must be wired by the LLM-settings route or deleted (P21).

## Two Python environments with different dependency versions

`/opt/homebrew/bin/pytest` (the command CLAUDE.md documents) runs **Python 3.11**
with an older installed dependency set; the app itself runs **Python 3.12**.
Measured 2026-09-15: requests 2.32.3 vs 2.31.0, fastapi 0.109.0 vs 0.115.5,
anthropic 0.75.0 vs 0.78.0, cryptography 45.0.7 vs 46.0.6. 3.11 also has no
PyQt6, so every GUI test is permanently skipped under the documented command.

Consequences worth deciding on:
- `requirements-web.txt` is pinned to the 3.12 set, which is what the app runs.
- A test cannot meaningfully assert "pins match what is installed here".
- CI on a blank machine is the only thing that proves the pins resolve, which is
  an argument for adding `.github/workflows/tests.yml` (a standing rule in
  `~/.claude/CLAUDE.md` for any repo with a suite pushed to GitHub).

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
