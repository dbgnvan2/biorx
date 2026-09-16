# Cold review — web-app addition, whole diff (a3769ef..HEAD)

Reviewed as inherited code, with no knowledge of how it was written or of the
commit messages. Scope: logic correctness, architectural coherence,
maintainability, and "would it actually work for a few colleagues". This pass
deliberately does **not** re-run a failure-pattern (P1–P35) sweep — those were
covered incrementally. Test state at review time: `431 passed, 8 skipped`.

Verdict up front: the addition is coherent, well-tested, and mostly
right-sized. There are **two genuine correctness bugs that a failure-pattern
sweep would not flag** (both silent), one real storage-model incoherence, and
one fragile producer/consumer contract. Everything else below is lower-order.

---

## 1. Correctness bugs in the job/search/summary flow

### 1.1 (HIGH) A repeat summary of an already-persisted paper is silently dropped

`web/routes_summaries.py:126-136`:

```python
paper_id = ctx.db.insert_paper(paper)
if paper_id:
    ctx.db.insert_summary(...)
```

`insert_paper` (`src/db.py:371-435`) returns `None` on a duplicate DOI — it
catches `sqlite3.IntegrityError` and does **not** look up the existing row id.
So the second colleague (or the same one, later) who summarizes a paper that is
already in `papers` gets:

- a job that reports `done`,
- the summary rendered to them,
- the owner key **billed** (usage finalized), but
- nothing written to `summaries`.

The `INSERT OR REPLACE` update path — which is the whole reason
`summaries.paper_id` is `UNIQUE` — never fires, because the code never reaches
`insert_summary` with a valid id. The returned `paper_id` is `None`, and the
result is silently unpersisted.

No test covers this: `test_a_summary_is_stored_with_its_model_and_author`
(`tests/web/test_summaries_routes.py:169`) only asserts the *first* summary
persists. The cap tests summarize the same `PAPER` three times but never check
`summaries`. The fix is to resolve the existing row by DOI and reuse its id
(or have `insert_paper` return the existing id on duplicate). This is the most
important finding in the review — it is silent data loss that also costs money.

### 1.2 (MEDIUM) Empty-summary validation is inconsistent between the two client families

Hosted clients funnel through `_coerce_summary` (`src/llm_providers.py:125`),
which raises when all three fields are empty. `OllamaClient.summarize_paper`
(`src/llm.py:163`) returns `{"key_findings": [], "methodology": "", "conclusions": ""}`
when its line-prefix parser finds nothing — and `web/routes_summaries.py:120`
only guards `summary is None`.

So an Ollama run whose model emits off-shape text (no `KEY FINDINGS:` block)
stores a **blank summary as success**. Reachable on any local run: `ollama` is
the `default_provider` in `llm_config.yaml`. The test
`test_an_ollama_none_return_is_an_error_not_a_blank_summary` covers `None` but
not the empty dict. The fix is to run the Ollama result through `_coerce_summary`
too (see §2.1 — the two paths should share one validator).

### 1.3 (MEDIUM) PDF storage contradicts the documented storage model

`src/pdf_handler.py:18` hardcodes `output_dir="~/preprints/PDFs"`. Every other
write path in the diff was made environment-driven (`default_db_path` reads
`BIORX_DB_PATH`/`DATA_DIR`; `paper_meta` reads `BIORX_CONTACT_EMAIL`), but
PDFHandler was left with a literal home path.

In the container, `_extract_text` (`web/routes_summaries.py:89`) therefore
downloads PDFs to the `biorx` user's ephemeral home, **not** `/data`. Two places
in the repo state otherwise:

- `Dockerfile:26` — "The database and any downloaded PDFs live on the mounted
  volume, never in an image layer."
- `README.md:156` — "Without it the database and any downloaded PDFs are lost
  on every redeploy."

Both are wrong for PDFs. This is not a hard break — the PDF is extracted
immediately and the extracted text is never persisted — but it is a genuine
coherence bug, a disk-growth path inside the image home, and it means the
"does a redeploy lose my PDFs?" question the docs answer is answered
incorrectly. Also: `from typing import Dict` is duplicated at the foot of the
file (`src/pdf_handler.py:184`), a leftover edit artifact.

### 1.4 (LOW) "Results page in as they arrive" is not true

`README.md:143-144` and the `search_results` docstring
(`web/routes_searches.py:172`) claim results are available mid-run. They are
not: `Job.result` is assigned only when the worker returns (`src/jobs.py:161`),
and the client loads results only on a terminal status
(`web/static/app.js:240-244`). `on_batch` accumulates into a closure list that
only becomes `job.result` at completion, so a mid-run paged fetch returns `[]`.
Not a crash — the client never relies on it — but the documented behavior and
the actual behavior disagree, and the phrase reads as a promise.

---

## 2. Duplicated logic that will drift

### 2.1 Two summarization prompt/parse implementations

`src/llm.py` (Ollama: `KEY FINDINGS:` line-prefix prompt + blank-line parser)
and `src/llm_providers.py` (JSON schema + `_coerce_summary` + `_extract_json`)
both produce the same `{key_findings, methodology, conclusions}` shape, but
validate it differently — which is exactly §1.2. The module docstring in
`llm_providers.py` justifies the new JSON path as replacing the old parser, yet
the old parser still runs for Ollama. Any change to the summary contract now has
two homes and two failure semantics. This is the highest-drift item in the diff.

### 2.2 The "source failed" contract is a string match on human-readable status text

The orchestrator has no structured failure channel: an unreachable source is
surfaced only as an `on_status` string like `"Europe PMC unavailable — skipped"`.
`web/routes_searches.py:41-56` re-parses that by hardcoding the marker
`"— skipped"` and importing the orchestrator's **private** `_SOURCE_LABELS` to
map the label back to a source name.

A round-trip test guards it, and the code comment acknowledges the drift risk
(P19), but this is the most fragile seam in the whole addition: reword a status
string in the orchestrator and "which sources were down" silently breaks, with
the failure invisible unless the round-trip test catches it. The durable fix is
a structured failure callback on the orchestrator rather than scraping prose.
This is worth scheduling even though it is guarded today.

### 2.3 PDF URL construction exists in more than one place

`src/paper_meta.py:55` builds the bioRxiv-style `…/content/{doi}v{version}.full.pdf`
fallback; `src/sources/biorxiv_medrxiv.py:125` and `src/sources/arxiv.py:265`
already compute the same thing at normalization time, and `schema.to_dict` carries
`pdf_url` through. Lower priority than 2.1/2.2 — the paper_meta function is the
right consolidation — but the "which URL wins" precedence now lives partly in the
adapters, partly in `paper_meta`, partly in Unpaywall enrichment.

---

## 3. Module boundaries

Mostly sensible. `web/` is a clean HTTP layer over `src/`, and the split of
`user_store` out of `db.py` is justified in its docstring. Specific issues:

- **`web/routes_searches.py:26`** imports `_source_label` from the orchestrator
  and never uses it (dead import), while line 52 reaches into the private
  `src.sources.orchestrator._SOURCE_LABELS`. Two cross-layer couplings to
  private symbols in one file.
- **Filters are split across two similarly-named modules**: `src/filters_store.py`
  (the shared `filters.json`, desktop) and `src/user_store.py` (per-user SQLite
  filters, web). Nothing in the names or docstrings makes the desktop-vs-web
  split obvious at a glance; the seeding path in `routes_session.py:90` also
  reads `"filters.json"` as a *relative* path, which only resolves if the
  process CWD is the repo root (fine in the container with `WORKDIR=/app`, but a
  local `uvicorn` launched from elsewhere seeds nothing, silently).
- **Dead surface a new maintainer must rule out**: `AppContext._extras`
  (`web/deps.py:49`), `JobRegistry.jobs_for` (`src/jobs.py:223`, exercised only
  by its own test), and `crypto.mask` (`src/crypto.py:118`, referenced only by
  its own test — `last4` is the one that got wired). Small, but each is a reason
  a reader pauses.

---

## 4. Is any of it over-engineered for "a few colleagues"?

On balance, no. The pieces that look heavy on first read are each load-bearing
for a *shared* access code:

- The atomic spend-cap reservation (`user_store.reserve_owner_usage`) closes a
  real check-then-act race, and `tests/web/test_cap.py:139` actually drives it
  with eight threads against a cap of three — the test fails on the naive
  version. Justified.
- The expired-job memory and TTL (`src/jobs.py`) turn "your search vanished"
  into "run it again" — a genuine usability need for the in-process-job design.
- The opaque-user-id / signed-cookie split and the "refuse, never auto-generate
  `KEY_ENC_SECRET`" stance are the right calls for storing colleagues' API keys.

The only trims I would make are the dead code in §3 (`_extras`, `jobs_for`,
`mask`, the unused import) and the duplicated `typing.Dict` import in
`pdf_handler.py`. The in-process `ThreadPoolExecutor` job registry is explicitly
and correctly argued in `src/jobs.py` — moving to Celery/Redis would be the
over-engineering.

---

## 5. What a user would immediately want that is missing

1. **Results are not persisted or re-findable.** A search's matches live only in
   memory on the `Job` and expire after the 1-hour TTL. There is no bookmark, no
   "save this paper", no search history in the web UI — even though `src/db.py`
   already has `bookmarks` and `search_history` tables, and the desktop app
   exposes Download/Bookmark buttons. For a colleague, "I found a paper
   yesterday, let me get back to it" means re-running the whole multi-minute
   search. This is the most likely first thing a real user will ask for.

2. **Persisted summaries are write-only from the web.** `GET /api/papers/{id}/summary`
   (`web/routes_summaries.py:215`) exists and is tested, but no UI path ever
   calls it. Every "Summarize" click re-runs the LLM — and re-bills the owner
   key up to the cap — rather than first checking for an existing summary.
   Combined with §1.1, the whole persistence layer is effectively dead weight
   from the user's perspective.

3. **No download button in the client.** The desktop app downloads PDFs; the web
   app only links out. Defensible as in-scope ("what the desktop app offers, and
   nothing more" — README), but note the summary flow already downloads the PDF
   server-side and then discards it.

These are product gaps, not defects — but they are the gap between "the thing
works" and "a colleague actually adopts it".

---

## 6. Is it readable without the commit messages?

Yes, and this is the strongest part of the work. Module docstrings carry a
`Purpose / Spec / Tests` triple; inline comments explain *why* rather than
restating the code. The reasoning for non-obvious choices (lazy `__getattr__`
app construction, the `Database.conn` holder lifetime, the entrypoint's
`needs_chown`) is written down at the point of the code.

Two readability caveats:

- The comments and docstrings cite `learnings P2/P14/P15/P19/P22/P28/P34…` and
  `standards L1/L4/L5/E1/E2/E5/S2/S7…`. Those documents live in
  `~/.claude/standards/` and a learnings log **outside this repo**. A new
  maintainer who clones only this repository cannot resolve the shorthand.
  `TODO.md` and the cycle docs do carry the substantive content, so it is
  navigable, but the references are a dependency on the author's external note
  system that will rot as soon as anyone else takes over. Worth a one-time pass
  to either in-repo those learnings or stop citing them by bare number.
- The PEP 562 lazy `app` (`web/app.py:104-110`) is clever and well-commented, but
  it is the first thing a newcomer will trip on when `uvicorn web.app:app`
  "works but `import web.app` doesn't build an app". The comment covers it.

---

## Summary of actions, in priority order

1. **Fix §1.1** — resolve the existing paper id on duplicate DOI so a repeat
   summary persists (and add a test that summarizes one paper twice and asserts
   the `summaries` row).
2. **Fix §1.2 / §2.1** — route the Ollama result through `_coerce_summary` so
   one validator owns the "is this a usable summary" decision.
3. **Fix §1.3** — make `PDFHandler.output_dir` environment-driven (`DATA_DIR`-aware)
   and correct the Dockerfile/README claims about where PDFs land.
4. **Schedule §2.2** — give the orchestrator a structured failure callback so the
   web layer stops parsing status prose.
5. **Clean §3** — delete the dead imports/fields/functions and the duplicate
   `typing` import.
6. **Decide §5** — the persistence/read-back gap (bookmarks, reuse of existing
   summaries, no re-findable results) is the visible-to-users half of this; the
   code already has the tables and endpoints for most of it.

None of the above changes the overall judgment: the feature is coherent and
would run, but §1.1 and §1.2 are silent-correctness bugs a colleague would only
notice as "my summary didn't save" / "I got a blank summary".
