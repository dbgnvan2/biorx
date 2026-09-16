# Making biorx GREAT — Differentiation Spec

Status: proposal, revision 3. Date: 2026-09-16.
Audience: the coding agent + Dave.

Thesis: biorx does not win by out-S2-ing Semantic Scholar (more papers, more
citations). It wins by combining things S2 structurally cannot offer —
(a) native per-source search precision, (b) identity resolution across the
preprint↔published divide, and (c) a personal research tool that runs on the
user's own machine: saved filters, monitoring, summaries with the user's own
model or key, and full-text search over *their own* library.

**Naming.** Phases are `GL` and `G0`–`G6`; items are `G1.2` etc. They are
deliberately not `P…`, which is the failure-pattern catalogue in
`~/.claude/standards/learnings.md`. References to that catalogue are written
"learnings P35".

---

## Decisions recorded (2026-09-16)

| # | Decision |
|---|---|
| D1 | **Single-user, local.** Each person runs their own copy; all data stays on their machine. Not a multi-user cloud service. |
| D2 | **Front end: the web UI, served locally** on `127.0.0.1` and opened in the user's browser. The PyQt GUI (`gui.py`) is frozen and retired once the web UI reaches parity (GL.5). |
| D3 | **Platforms: macOS and Windows.** |
| D4 | **Retire the Railway deployment** and the multi-user code: access code, session users, per-user encrypted keys in the database, owner-key spend cap, Dockerfile, `railway.json`, `docker-entrypoint.sh`. |
| D5 | Europe PMC "both" field defaults to title/abstract; full-text search is an explicit option (G0.1). |
| D6 | Scheduled runs never call a paid LLM automatically (G3.2). |

Revision 2's G2 (per-user scoping) is removed: with one user per install there
is nothing to scope.

---

## 0. Corrections and verified facts (read first)

Each item was checked against the code, or against a live API, on 2026-09-16.

Confirmed:

- **OpenAlex is not a search adapter.** `src/sources/config.py` lists it with
  `enabled: False` and `SOURCE_LABELS` names it, but there is no
  `src/sources/openalex.py`. OpenAlex *is* already called for abstract recovery
  (`src/paper_meta.py:fetch_openalex_abstract`, N2).
- **Dedup is hand-rolled** in `src/sources/dedup.py`. Identity precedence is
  normalized DOI → PMID → PMCID → title + first-author surname + year. No NFKC
  normalization; no arXiv-id index.
- **Ranking has no citation signal and no relevance signal.**
  `orchestrator._rank()` is trust weight + DOI + abstract + OA − retraction
  penalty.
- **No citation graph, no related-papers, no "cited by".**
- **`psyarxiv.py` and `socarxiv.py` are near-identical** (diff: docstrings,
  class name, provider slug).

Corrections to revision 1:

- **`canonical_id` is not "`doi:`-or-nothing".** `make_canonical_id`
  (`src/sources/schema.py:153`) falls back DOI → PMID → PMCID → title hash; the
  arXiv adapter sets `arxiv:<id>`. Since N1 (`d278e8d`) it is the unique database
  identity of a paper. Replacing it with an external id is withdrawn (G1.1).
- **Europe PMC is already searched across full text, unintentionally.** The
  query builder sends the "both" field as bare terms (`query_builder.py:55`,
  whose comment claims title-or-abstract). Live check, 2026-09-16:
  `ketamine AND "default mode network"` → **1,577** hits;
  `TITLE_ABS:(ketamine AND "default mode network")` → **54**.
- **The retrieval layer is locked by a test.** `tests/web/test_no_retrieval_drift.py`
  fails on any change to the orchestrator, dedup, query builder, schema and
  adapters since baseline `a3769ef`. See §4.
- **Nothing stores extracted PDF text.** `pdf_handler.extract_text` runs on demand.

Facts that shape the local conversion:

- **The web UI does not yet do what the desktop GUI does.** Web routes today:
  filters CRUD, searches (start/poll/results/cancel), summaries
  (start/poll/lookup/get), session and LLM key. Desktop-only: paper detail view
  (abstract, discussion, open PDF/page, load local PDF), PDF download (single,
  selected, all, stop), saved reference lists (save selection, view, remove,
  delete list), Excel export, filter editor extras (OR groups, test filter,
  save-as), run-selected / run-all-enabled.
- **Two filter stores exist.** Desktop: `filters.json`. Web: the `user_filters`
  table.
- **Data location** defaults to `~/preprints/` (`src/db.py:26`,
  `src/pdf_handler.py:15`), overridable by `BIORX_DB_PATH` / `DATA_DIR`.

To verify before building on it (not checked):

- **OpenAlex access terms.** It may now require an API key with a free daily
  allowance. Check before G1.3/G5.1.
- **Which preprint servers are still on OSF.** ChemRxiv, EarthArXiv and engrXiv
  may have left. Use `https://api.osf.io/v2/preprint_providers/`.
- **SQLite FTS5 in the Windows Python build.** Expected present; confirm in CI.

---

## 1. What Semantic Scholar users complain about

**Evidence status: anecdotal.** Gathered from forum posts and reviews
(2024–2025); links were not recorded and the claims have not been re-verified.
Add a link to each row before this section is cited anywhere else.

| # | Complaint | Source (unlinked) | biorx's answer |
|---|---|---|---|
| C1 | Rate limits — ~1 req/s with a key; projects hit 429s | GitHub issue, SakanaAI/AI-Scientist | S2/OpenAlex for sparse lookups only, never the search path |
| C2 | Weak social-science coverage vs Google Scholar | Reddit r/research | Native PsyArXiv/SocArXiv; more OSF servers (G4.1) |
| C3 | Older, highly cited papers dominate. (S2's default sort is relevance; the complaint is that citations weigh heavily in it.) | blog post, Quora | Citations as one bounded signal, with recency (G5.1) |
| C4 | Fewer results than Google Scholar / nothing relevant | Reddit r/academia | Native multi-source + `sources_failed` transparency; library search (G5.3) |
| C5 | UI complaints | Reddit r/research | Product decision, not code |
| C6 | Retrieval returns metadata, not full text | Reddit r/Rag | Local PDF download and text extraction (already does) |
| C7 | No saved-search monitoring or personalization | (absence) | Saved filters + monitoring with deltas (G3) |

**S2 has no notion of "you".** biorx remembers what a user is looking for, keeps
their library on their machine, and tells them when something new appears.

---

## 2. The moat — do not regress these

1. **Native source queries** — Lucene with species clauses (Europe PMC/PubMed),
   date-windowed streams (bioRxiv/medRxiv).
2. **Source-trust ranking** with retraction penalty.
3. **Local-first summarization** — Ollama on the user's machine; DeepSeek or
   Anthropic with the user's own key.
4. **PDF download + local full-text extraction.**
5. **Saved filters + monitoring.**
6. **Transparency** — `sources_failed`, "empty ≠ quiet week", retraction flags.
7. **Stable paper identity** — a stored paper keeps its `canonical_id` (N1).
8. **The user's data stays on the user's machine.** No telemetry, no hosted copy.

---

## 3. Prerequisite: the approved backlog, re-scoped by D1–D4

`docs/implementation_plan_2026-09-16_backlog.md` is approved and in progress (N1,
N2 done; batch H next). D1–D4 change some of its batches. The backlog plan must
be amended (a separate commit, with Dave's approval) as follows:

| Batch | Effect of D1–D4 |
|---|---|
| H (CI) | Matrix becomes `macos-latest` + `windows-latest`. Contact-address item unchanged. |
| A (dead code) | `crypto.mask()` removal becomes part of GL.4, which removes `crypto.py`. Rest unchanged. |
| B (desktop `llm.py`) | Unchanged — the legacy summarizer is still used by `agents/`. |
| C, D (adapters, arXiv, monitor CLI) | Unchanged. |
| E (tests) | Constant-time access-code test is dropped with the access code (GL.3). Env-var test unchanged. |
| F (container and access) | Entrypoint hint, session rate limit and "created_by_user_id" decision are dropped. CSP header is kept (still useful on localhost). Per-user job cap becomes a global job cap. |
| G (desktop threading) | Dropped once `gui.py` is retired; do only if the GUI stays in use for long before parity. |
| I (standards repo) | Unchanged. |

**Finish backlog H, A, D and C before G1** — D and C change the same adapters.

---

## 4. The retrieval lock

Items marked **[unlocks W1.a]** change files protected by
`tests/web/test_no_retrieval_drift.py`. For each such item, the commit updates
`BASELINE` in that test to the new commit's parent and says in its message which
protected file changed and why. The lock is re-baselined one item at a time,
never removed.

---

## 5. The spec

### G0 — Search precision bug (small, do first)

**G0.1 Europe PMC "both" field searches full text by accident.** [unlocks W1.a]
Per D5: send "both" terms as `TITLE_ABS:` in `_group_to_lucene`, fix the comment,
and add a per-filter `search_full_text` option (default off) that sends bare
terms.
- Existing saved filters return fewer hits. Announce it in the changelog and
  README; do not change saved filters silently.
- Check the PubMed adapter, which shares `build_europepmc_query`.
- Acceptance: `test_g0_1_both_field_uses_title_abs_by_default`;
  `test_g0_1_full_text_option_sends_bare_terms`; live hit-count comparison
  recorded in the gate file (integration-only, flagged as such).

### GL — Local single-user app (D1–D4)

**GL.1 Local launcher.** One command (`biorx`, plus a double-clickable launcher
per OS, GL.6) starts the server on `127.0.0.1`, picks a free port if the default
is taken, and opens the browser.
- Prints the effective URL, port, data directory and database path on startup
  (learnings P16).
- Data directory: if `~/preprints/` exists, keep using it (no data moves). Else
  the OS-standard location (`platformdirs`: `~/Library/Application Support/biorx`
  on macOS, `%LOCALAPPDATA%\biorx` on Windows). `BIORX_DB_PATH` / `DATA_DIR`
  still override. All paths through `pathlib`; no hardcoded `/` or `~` strings.
- A second launch while one is running opens the browser on the running
  instance instead of starting another server on the same database.
- Acceptance: `test_gl_1_existing_preprints_dir_is_kept`;
  `test_gl_1_platform_default_when_absent` (both OS branches, patched);
  `test_gl_1_port_in_use_picks_another_and_reports_it`;
  `test_gl_1_second_launch_reuses_running_instance`.

**GL.2 Local-server security (replaces the access code).** A server on localhost
without a login is reachable by any web page the user visits, through their
browser (cross-site requests, DNS rebinding). So:
- Bind `127.0.0.1` only, never `0.0.0.0`.
- Reject requests whose `Host` header is not `127.0.0.1:<port>` or
  `localhost:<port>`.
- A random token is generated per launch, put in the URL the launcher opens,
  exchanged for a `SameSite=Strict`, `HttpOnly` cookie, and required on every
  `/api` route. No CORS headers.
- Acceptance: `test_gl_2_foreign_host_header_is_refused`;
  `test_gl_2_api_without_token_is_401` (every protected route, exact route count
  as in the existing route-auth test); `test_gl_2_server_binds_loopback_only`.

**GL.3 Remove the multi-user and cloud code.**
- Delete `Dockerfile`, `railway.json`, `docker-entrypoint.sh`,
  `tests/web/test_deploy_files.py`, and the README deploy section.
- Remove the access code, `/api/session` user creation, display names, and the
  owner-key spend cap (`usage_events` counting against a cap). Keep an optional
  local record of LLM calls if useful for the user's own cost tracking — a
  decision for the plan, not required.
- Schema: no destructive migration. Existing `user_id` columns stay; the code
  uses one fixed `LOCAL_USER_ID`. Rows written under any earlier web user id are
  reassigned to it once, with counts logged before and after.
- Acceptance: `test_gl_3_no_access_code_setting_remains` (config/env scan by
  parsed settings, not substring); `test_gl_3_existing_user_rows_are_reassigned`
  (populated DB, exact counts); the removed routes return 404.

**GL.4 API keys in the OS credential store.** Store DeepSeek/Anthropic keys with
`keyring` (macOS Keychain, Windows Credential Manager). Environment variables
still take precedence. No key, and no part of one beyond the last 4 characters
for display, is stored in the database.
- Migration: if a key is stored encrypted in the database and the secret to
  decrypt it is available, move it into `keyring` and clear the column; otherwise
  clear the column and ask the user to re-enter the key, saying why.
- Then remove `src/crypto.py` and its tests.
- If `keyring` has no usable backend, say so and fall back to env vars only —
  never to plaintext on disk.
- Acceptance: `test_gl_4_key_is_saved_to_keyring_not_db`;
  `test_gl_4_env_var_overrides_keyring`; `test_gl_4_db_key_migrates_and_column_is_cleared`;
  `test_gl_4_no_backend_does_not_write_plaintext`.

**GL.5 Feature parity, then retire `gui.py`.** Build the desktop-only features
(§0) into the web UI. The plan must contain a parity table with one row per
feature and a test per row, asserting the front end actually passes each control
through to the backend (learnings P25).
- Unify filters: one store (the database). On first launch, import
  `filters.json` once, reporting "imported N of M"; duplicates by name are kept
  and renamed, not dropped.
- Reference lists: fix `reference_list_items UNIQUE(list_id, doi)`, which allows
  unlimited duplicates of papers without a DOI (the N1 class); key on
  `canonical_id`.
- `gui.py` stays runnable and frozen (bug fixes only) until every parity row is
  done, then is removed with `run_gui.sh` and PyQt from requirements.
- Acceptance: the parity table's tests; `test_gl_5_filters_json_imported_once`
  (dirty state: second launch imports nothing);
  `test_gl_5_doi_less_paper_added_twice_is_one_item`.

**GL.6 Install and launch on macOS and Windows.** Scripted install per OS
(`install.sh`, `install.ps1`) that installs a pinned Python environment, the
dependencies and a launcher (a `.command` file on macOS, a Start-menu/desktop
shortcut on Windows). Optional Ollama is detected and explained, not required.
- A packaged app (PyInstaller `.app`/`.exe`, signing, notarization) is out of
  scope for this revision; see §7 open decision O1.
- Acceptance: CI job per OS runs the install script on a clean runner, launches
  the server, and calls a health route (integration); written install
  instructions tested once by hand on each OS (human review).

**GL.7 CI on both platforms.** GitHub Actions matrix `macos-latest` and
`windows-latest` running the full suite (amends backlog batch H). Every check
lives in the suite, not the workflow.

### G1 — Identity foundation

**G1.1 Keep `canonical_id` stable; store other ids beside it.** Add nullable
`arxiv_id` and `openalex_id` columns to `papers` via `_add_column_if_missing`,
indexed. `canonical_id` is set once when a paper is first stored and never
rewritten by enrichment. Lookups may resolve through any stored id.
- Identity must not depend on whether an external call succeeded (learnings P1).
- Acceptance: `test_g1_1_canonical_id_unchanged_after_enrichment`;
  `test_g1_1_lookup_by_arxiv_id_finds_doi_keyed_paper`; dirty-state test on a
  populated DB (learnings P8).

**G1.2 Dedup additions within one search.** [unlocks W1.a] Add an arXiv-id index
after PMCID (normalize `arXiv:2106.12345v2` → `2106.12345`) and NFKC-normalize
`_norm_title`. **No ±1-year tolerance** — it merges corrections, errata and
conference abstracts with their articles.
- Acceptance: `test_g1_2_arxiv_prefix_and_version_dedup`;
  `test_g1_2_nfkc_title_match`; adversarial
  `test_g1_2_correction_notice_does_not_merge_with_article`.

**G1.3 Link preprints to published versions; do not merge them.** [unlocks W1.a]
Add a `paper_links` table (`from_canonical_id`, `to_canonical_id`, `relation` =
`published_as`, `source`, `found_at`). The UI shows "Published as …" /
"Preprint version …".
- Why not merge: `_merge` makes `is_preprint` sticky, so a merged article would
  be labelled a preprint; the versions can also differ in content.
- Sources, cheapest first: (1) bioRxiv/medRxiv API `published` field;
  (2) Crossref `relation.is-preprint-of` / `has-preprint`; (3) OpenAlex, only if
  1–2 give nothing and its terms are verified.
- A failed lookup records nothing and stays retryable; never "no published
  version" (learnings P1).
- Acceptance: `test_g1_3_biorxiv_published_doi_creates_link` (recorded real
  response); `test_g1_3_linked_versions_remain_two_rows`;
  `test_g1_3_lookup_failure_writes_no_link`.

### G3 — Monitoring (the differentiator; attacks C7)

**G3.1 New-since-last-run.** Store, per filter, the `canonical_id`s its last run
produced, that run's `complete` flag (true only if no source failed), and the
id-scheme version.
- If either run is incomplete, compare against the last *complete* run and say
  so. Never report a paper as new only because the baseline could not be read;
  never treat a paper missing from an incomplete run as removed (learnings P35,
  P31).
- If the id-scheme version changed (any G1 change), the first run after it
  reports "baseline reset", not N new papers.
- A preprint whose published version appears is reported as "now published",
  not as new (uses G1.3).
- Acceptance: `test_g3_1_failed_source_does_not_inflate_new_count`;
  `test_g3_1_first_run_after_id_change_reports_reset`;
  `test_g3_1_second_run_reports_only_additions` (dirty state).

**G3.2 Scheduled searches on a machine that is not always on.** Schedules live in
a `filter_schedules` table (`filter_id`, `cadence`, `last_run_at`,
`next_run_at`). Two triggers, one code path:
1. **On launch:** any schedule that is due runs once (catch-up), in the
   background, with progress shown.
2. **Optional OS scheduler:** the user can turn on a background entry — a
   launchd agent on macOS, a Task Scheduler task on Windows — that runs a
   headless `biorx run-due` command while the app is closed. Turning it off
   removes the entry.
- Both triggers claim a run with a conditional database update
  (`UPDATE … WHERE next_run_at = ?`), so the app and the OS task never run the
  same schedule twice.
- Results appear in the in-app "new since last run" feed. An OS notification
  ("3 new papers for <filter>") is optional.
- Per D6, scheduled runs never call a paid LLM.
- Acceptance: `test_g3_2_due_schedule_runs_once_on_launch`;
  `test_g3_2_concurrent_triggers_run_once`;
  `test_g3_2_scheduled_run_calls_no_llm`;
  `test_g3_2_os_entry_install_and_remove` (command construction per OS, patched;
  the real registration is integration-only).

**G3.3 Summarize the new arrivals.** One action on the feed summarizes the N new
papers with the user's chosen provider. Before running, show N, the provider and
model, and — for paid providers — that N calls will be billed to the user's key;
the user confirms.
- Acceptance: `test_g3_3_batch_requires_confirmation_for_paid_provider`;
  `test_g3_3_one_failure_does_not_stop_the_batch_and_is_reported`.

**G3.4 Citation export.** BibTeX, RIS and CSL-JSON from a reference list,
alongside the existing Excel export.
- Papers without a DOI export with arXiv id, PMID or URL; none is silently
  skipped. Report "exported N of M" if any cannot be written.
- Acceptance: round-trip test parsing each format back, entry count equals list
  size (learnings P19); `test_g3_4_doi_less_paper_exports`.

### G4 — Coverage (attacks C2)

**G4.1 One OSF adapter, parameterized by provider.** [unlocks W1.a] Replace
`psyarxiv.py` and `socarxiv.py` with `osf.py` taking a provider slug; keep the
`psyarxiv` / `socarxiv` source names so saved filters still work. Enable more
servers from the verified OSF list, one config entry each.
- OSF has no server-side text search; adapters page by date and filter client
  side. Log "fetched N pages, kept M of K records" per server and announce any
  page cap (learnings P9).
- Acceptance: existing PsyArXiv/SocArXiv adapter tests pass unchanged against
  `osf.py`; `test_g4_1_saved_filter_with_psyarxiv_still_runs`.

### G5 — Relevance & discovery (attacks C3, C4, C6)

**G5.1 Citation count and recency in ranking.** [unlocks W1.a]
- Relevance input: each record's position in its source's own result order,
  normalized per source.
- Citations: `log1p(cited_by_count)`, capped in weight. A failed lookup is
  unknown and gets the neutral value, not zero (learnings P1/P35).
- Recency: exponential decay by age, half-life in config.
- All weights in `sources_config.yaml`. Retraction penalty stays larger than any
  positive signal.
- Citation lookups are batched, cached via `src/sources/cache.py`, and
  time-boxed; ranking proceeds without them if they fail.
- Acceptance: adversarial
  `test_g5_1_old_highly_cited_irrelevant_paper_ranks_below_recent_relevant`;
  `test_g5_1_failed_lookup_is_neutral_not_zero`;
  `test_g5_1_retracted_paper_ranks_last_regardless_of_citations`;
  `test_g5_1_weights_come_from_config`.

**G5.2 "Cited by" and "Related".** Lazy, cached per-paper lookups when a paper is
opened (OpenAlex `cited_by` / `related_works`, or S2 recommendations). A failed
lookup shows "couldn't load", never "no citations".
- Acceptance: `test_g5_2_lookup_failure_is_distinct_from_empty`.

**G5.3 Search within the library.**
1. Persist extracted text in a `paper_text` table (`paper_id`, `text`,
   `extracted_at`, `pages`), written when a PDF is extracted, and index it with
   abstracts in SQLite FTS5. Works for every user.
2. Semantic search, available when Ollama is installed: `nomic-embed-text`
   embeddings stored in SQLite, brute-force cosine. Without Ollama the option is
   shown as unavailable with the reason. No vector database.
- Acceptance: `test_g5_3_extracted_text_is_persisted`;
  `test_g5_3_fts_finds_term_only_in_full_text`;
  `test_g5_3_semantic_search_reports_missing_ollama`; embedding quality is
  integration-only, flagged.

### G6 — Hardening

**G6.1 Local backups.** Back up the database to a folder the user chooses (which
may be a synced folder such as iCloud Drive or OneDrive), on launch when the last
backup is older than a configured number of days, and on demand.
- Use SQLite's backup API or `VACUUM INTO`, never a raw file copy of a live
  database. PDFs optional (they can be re-downloaded).
- Keep the last N backups (config). A failed backup is shown in the UI, not only
  logged.
- After GL.4 the database holds no API keys; the test below guards that.
- Restore: a documented, tested "restore from backup" action.
- Acceptance: `test_g6_1_backup_during_writes_is_consistent`
  (`PRAGMA integrity_check`, exact row counts);
  `test_g6_1_backup_contains_no_api_key`; `test_g6_1_restore_round_trip`;
  `test_g6_1_retention_keeps_n`.

---

## 6. What is deliberately *not* in this spec

- **Hosting, multi-user access, accounts.** D1, D4.
- **Sync or sharing between colleagues.** Each install is independent. Sharing a
  filter or reference list by exporting a file is a possible later item.
- **A packaged, signed installer.** Open decision O1.
- **Full S2 corpus replacement**, **a vector database**, **merging preprints with
  published versions**, **replacing `canonical_id`**, **becoming Google Scholar.**

---

## 7. Open decisions

| # | Question | Recommendation |
|---|---|---|
| O1 | Install method for colleagues: scripted install (GL.6), or a packaged `.app`/`.exe` | Scripted install first. A packaged app needs code signing (Apple Developer account, Windows certificate) to avoid OS security warnings; do it only if colleagues cannot manage the script. |
| O2 | Keep the legacy CLI agents (`agents/search_agent.py`, `summarization_agent.py`, `monitor.py`) or fold them into `biorx run-due` and friends | Fold `monitor.py` into `biorx run-due` (G3.2); decide the other two in the plan. |
| O3 | Record the user's own LLM calls locally for cost tracking (GL.3) | Yes, as a simple history with no cap. |

---

## 8. Build order

| # | Item | Why here |
|---|---|---|
| 1 | Amend backlog plan (§3); backlog H (macOS + Windows CI), A, D, C | In progress; D/C touch the same adapters |
| 2 | G0.1 | Live precision bug in every Europe PMC/PubMed search |
| 3 | GL.1, GL.2, GL.3, GL.4 | The local app is the platform everything else ships on |
| 4 | GL.5, GL.6 | Parity and install; then retire `gui.py` |
| 5 | G1.1, G1.2, G1.3 | Identity settled before deltas depend on it |
| 6 | G3.1, then G3.2 | The main differentiator |
| 7 | G6.1 | Before users rely on accumulated data |
| 8 | G5.1 | Needs OpenAlex terms verified |
| 9 | G3.3, G3.4, G4.1, G5.2, G5.3 | As time allows |
