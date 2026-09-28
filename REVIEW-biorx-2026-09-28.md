# Review: biorx (full repo, excluding gui.py) — 2026-09-28

## Verdict

The security base of the web app is solid. `safe_fetch` meets P8, the front end has no `innerHTML` sinks (P9), SQL is parameterised throughout, the session cookie and PIN lockout are sound, and the owner-key cap is reserved atomically. The biggest risk is the search results themselves. At default settings, a broad filter silently never queries five of the six sources. Several filter facets can never match. The OSF sources see only the first word of the filter. Concurrent searches share pagination state. Any of these can return a wrong answer while looking normal. The second risk is shared-data integrity: any signed-in user can replace the one shared summary of any paper, and a filter rename can silently destroy another filter. Fix the retrieval defects first (B1, B2, B3, B6), then the server-side guards that currently live only in `app.js`.

## Coverage

| Area | G-CORRECT | G-SEC | G-STRUCT | G-TEST |
|---|---|---|---|---|
| Web backend (web/*.py, jobs, access_codes, accounts, user_store, tokens, crypto, filters_store, selection, safe_fetch, env_file, deploy files) | covered | covered | covered (accounts, crypto, tokens, safe_fetch searched only) | partial |
| Retrieval (src/sources/*, filtering, discover, paper_meta, fulltext, biorxiv_api, pdf_handler, agents/search_agent, agents/monitor, configs) | covered | covered | partial (several adapters searched only) | partial |
| LLM and data (db, llm, llm_providers, llm_config, review, summary_pdf, reference_export, agents/summarization_agent) | covered | covered | covered | partial |
| Front end (web/static/app.js, index.html, styles.css) | covered | covered | partial (~400 of 2,886 lines read, the rest searched) | partial (frontend_wiring tests sampled) |

Read: 8 review workers and 2 challenge workers read ~18k LOC of production code plus ~17 of the 60 test files in full or in part. The suite at review time: 1336 passed, 1 skipped (local venv).

Not reviewed: `gui.py` (2,536 lines; the legacy PyQt6 app, which is being retired) and its tests (`test_gui_filters.py`, `test_source_picker.py`). About 40 test files were not opened by the test lens; see G-TEST notes. `src/tokens.py` accounting correctness was not checked. Live provider, finder and Unpaywall/Crossref behaviour was not exercised, so claims about how external APIs react to malformed params are unverified.

Excluded: `venv/`, `__pycache__/`, `docs/`, `tests/fixtures/`, the PDF in the repo root, markdown docs.

## Findings

Legend: **V** = verified in the challenge pass, **V-partial** = partly verified (the partial part is noted), **×n** = found independently by n lenses.

### Blockers

**B1 · CORRECT · `src/sources/orchestrator.py:212,255-256` · V ×1**
`max_results` is one shared budget of pre-filter records across all sources, and the loop `break`s when it is spent. Europe PMC is queried first, and web and monitor default to 200. Any filter with more than 200 Europe PMC hits therefore never queries PubMed, PsyArXiv, SocArXiv, bioRxiv or arXiv, and no status message says so. `MAX_PAGES_PER_SOURCE` (1,000 records) also cuts silently, but only above `max_results=1000`.
*Fix:* emit a `FAILURE_STATUS_MARKER` status for every skipped or truncated source. Make the budget per source, or count it against matched records. Test: source 1 fills the budget, and source 2 must be queried or reported.

**A1 · SEC/DATA · `web/routes_summaries.py:201-251,284-296`, `src/db.py:720` · V ×4 (web G-SEC, ingest G-SEC, web G-CORRECT, LLM G-CORRECT)**
POST `/api/summaries` trusts a client-built paper dict. With a real DOI, an invented title (so `text_is_this_paper` rejects every real PDF) and any abstract, the client abstract becomes the paper's single shared summary. `INSERT OR REPLACE` replaces any existing summary, including a paid full-text one, for every user, and it flows into their reviews and PDFs. A transient full-text lookup failure (P1) downgrades a good summary the same way. The only "keep existing full-text summary" check is in `app.js:1151-1158` (P10). `insert_paper` is first-writer-wins, so a user can also plant title, abstract and URLs for a DOI nobody has stored yet.
*Fix:* never let an abstract-only write replace `source_text='full_text'` (enforce in `insert_summary`). Take the stand-in abstract from the stored or server-looked-up record, not the request body. Re-resolve paper metadata server-side before `insert_paper`. *Question for the author:* was "one summary per paper, shared" meant to trust every invited user to write shared content?

### Major

**B2 · CORRECT · `src/sources/query_builder.py:169-177` · V**
`get_date_range` uses `dict.get(key, default)`, so the `"start_date": ""` / `"end_date": ""` saved in six `filters.json` filters (and sent by `app.js:787-789`) give empty dates rather than a `days_back` window. arXiv gets `submittedDate:[0000 TO 2359]`, and OSF gets empty `date_created` params. `normalise_filter` does not fix it. Europe PMC is unaffected (`_date_clause` uses `if not`).
*Fix:* `filter_dict.get("end_date") or today`, and the same for start. Test with empty-string dates for arXiv and OSF.

**B6 · CONCUR · `web/deps.py:61-63`, `src/sources/europepmc.py:104-156`, `src/sources/arxiv.py:72-79` · V**
One `SourceOrchestrator` and its adapters are shared by 4 job threads. Adapters keep `_cursor_mark`, `_last_query`, `last_total` and `last_page_size` on `self`, and there is no lock. Two concurrent searches reset or share each other's Europe PMC cursor, so pages repeat or are skipped. The shared `requests.Session` and the arXiv 3-second spacing are also not thread-safe.
*Fix:* keep pagination state local to each `search()` call (or build adapters per call). Add a two-thread interleaved-page test.

**B3 · CORRECT · `src/sources/psyarxiv.py:73-75`, `src/sources/socarxiv.py:56-58` · V**
Only `query.split()[0]` (the first word of the first term across all groups) is sent as `filter[title]`. For "Loneliness", every "social isolation" preprint on PsyArXiv and SocArXiv is lost server-side, and `filter_papers` cannot recover them. Nothing reports this.
*Fix:* one request per term, merged, or drop `filter[title]` and rely on the date range plus client filtering.

**B5 · DATA · `src/filtering.py:152-164`, `src/sources/schema.py:72-76`, `web/static/app.js:14-27` · V-partial ×2 (retrieval CORRECT, STRUCT)**
`paper_type` values ("review article", "new results" …) are never substrings of `document_type` (article/preprint/review/trial/other), so every non-`(any)` value matches nothing. The saved "Loneliness" filter (`paper_type: "review article"`) always returns 0. `author_corresponding_institution` is always `""`, so any institution filter returns 0. *Partial:* `license` does match bioRxiv's raw values but not Europe PMC, OSF or Unpaywall forms, and filtering runs before enrichment, so Unpaywall licenses are never seen.
*Fix:* one vocabulary file with stable ids mapped to CanonicalRecord values (see S3). Hide or warn on facets no source can supply. Test every option against records from each adapter.

**B7 · CORRECT · `src/sources/biorxiv_medrxiv.py:59-66` · V**
The adapter ignores `start_date`/`end_date` (it calls `search_recent(days=days_back)`) and hard-codes `server="biorxiv"`. medRxiv is never searched, and a range filter with `days_back: 0` searches only today.
*Fix:* use `get_date_range()` with `search_by_date_range`, and query both servers.

**B4 · CORRECT · `src/filtering.py:97-98` · V**
`adolescen*` is matched with `text.startswith(prefix)` against the whole title or abstract, so "Stress in adolescents" is dropped on every front end. A wildcard term ends up stricter than the plain substring `adolescen`.
*Fix:* match at word boundaries. Add an adversarial mid-title test.

**A2 · DATA · `web/routes_filters.py:40-64`, `src/user_store.py:382-389`, `app.js:1661-1693` · V ×2 (front end, web CORRECT)**
Renaming filter A to the existing name B upserts onto B (overwriting B's contents) and then deletes A. The response is a success. POST with an existing name replaces that filter and returns 201. Only the term-chip path avoids collisions (P10).
*Fix:* return 409 from PUT and POST on a name held by another id. Check client-side too. Add route tests.

**A4 · DATA · `web/static/app.js:1459` · V**
`loadFilterTab` redraws the source picker with the defaults on every return to the Filters tab while a saved filter is still open. The next Save or chip click replaces that filter's sources.
*Fix:* redraw only when `state.activeFilterId` is null, or else call `selectFilter`.

**A3 · CORRECT · `web/static/app.js:1645-1659` · V-partial**
`newFilter` does not reset category, paper type, version, published, license or species, so a new filter, including one created at once by a term-chip click, inherits the previous filter's restrictions. *Refuted part:* dates are not inherited (the range toggle is reset).
*Fix:* reset the six selects to `(any)`. Test that `buildFilterDict()` after `newFilter()` has none of those keys.

**A5 · SEC · `web/static/app.js:286-293,164-172` · V**
The browser-stored LLM key (`biorx_local_key`) survives sign-out and 401. The next person on that browser runs billed calls on the previous user's key and sees its last four characters.
*Fix:* call `clearLocalSettings()` in `signOut` and on the 401 path, or key the storage by user id.

**A8 · ERROR · `src/db.py:740-743` and callers `web/routes_summaries.py:248,286-296`, `agents/summarization_agent.py:204,241-253` · V ×2**
`insert_summary` returns `None` on `sqlite3.Error` (e.g. "database is locked"). No caller checks it, and a `None` from `_paper_row_id` is not checked either. A billed summary that was not saved is reported as done, and the next lookup bills again (P2).
*Fix:* have `insert_summary` raise, or check the return value and report "shown but not stored".

**A7 · CONCUR · `web/static/app.js:1140-1145`, `web/routes_summaries.py:342-377`, `src/jobs.py:162-175` · V**
The busy state of billed buttons (Summarize, batch, Review, Discover) is held only in page memory. It is not restored after a reload, and the server does not dedupe a second job for the same user and paper. A reload during a run lets a second click bill again. This breaks the global rule for background-launch buttons.
*Fix:* refuse a second summary job for the same (user, paper) with 409 while one is running. Expose running jobs so the page can restore the disabled state on load.

**A6 · CONCUR · `web/static/app.js:834-891` (pollSearch), `1708-1764` (pollFilterTest) · V**
Pollers never check which job a response belongs to, and `api()` has no timeout. A late response for job A can clear B's interval and render B as finished with empty results. The filter-test path is more exposed: its success branch has no guard, and the Test button is never disabled.
*Fix:* capture the job id at poll start and discard responses for a different id. Add an in-flight flag.

**A11 · ARCH · `src/db.py:756-763` · V ×2 (STRUCT, LLM CORRECT)**
`get_unsummarized_papers` requires `downloaded = TRUE`, but the only writer (`update_paper_path`) has no production caller, not even `gui.py`. `summarization_agent.py` / `./run.sh summarize` always finds 0 papers and reports success.
*Fix:* select papers with no summary row (or with `source_text='abstract'`) and drop the `downloaded` condition. Order by attempt so papers that always fail don't take up the limit.

**A12 · ARCH · `agents/summarization_agent.py:35`, `agents/search_agent.py:29` · V ×2**
The hard-coded `db_path="~/preprints/biorxiv.db"` skips `default_db_path()`, so `BIORX_DB_PATH` and `DATA_DIR` are ignored. In the container the agents write outside the `/data` volume.
*Fix:* default to `None`.

**A10 · CORRECT · `src/llm.py:149-157` · V**
The Ollama parser splits sections only on blank lines. With single-newline sections, every line goes into `key_findings`, and `_coerce_summary` still accepts the result. `lstrip("- ")` turns "-5% change" into "5% change". The parser is live for the `ollama` provider.
*Fix:* parse headers with a line-anchored regex, strip only one bullet marker, and raise if a header is missing. Better: move Ollama to the shared JSON prompt (S6).

**B8 · SEC · `src/paper_meta.py:145,176-178` · V**
The abstract-scrape regexes (`<(?:section|div|p)[^>]+…(.*?)</…`) backtrack quadratically on hostile HTML of up to 5 MB, fetched from a client-supplied URL. Python's `re` holds the GIL, so the whole server stalls. The size cap and SSRF guard are real but do not bound regex time.
*Fix:* use the existing `html.parser` extractor, or bound the quantifiers and cap the input at ~512 KB.

**B9 · SEC · `src/fulltext.py:276-288`, `src/pdf_handler.py:127-133` · V**
Untrusted PDFs of up to 100 MB (up to 4 per summary) are parsed in-process with no `max_pages`, no memory limit and no timeout. Only ~12,000 characters are used.
*Fix:* pass `max_pages` and stop once the text budget is reached. Extract in a subprocess with `RLIMIT_AS` and a timeout.

**B10 · DEPS · `requirements-web.txt` · V-partial ×2 (web G-SEC, ingest G-SEC)**
*Verified:* pins are `pdfplumber==0.10.3` (→ `pdfminer.six==20221105`), `requests==2.31.0` and `urllib3==2.2.2`. The local venv runs `urllib3 2.6.3`, `certifi 2026.2.25` and `pytest 9.0.2`, so the comment "pinned to what this machine has been verified against" is false, and CI and the container run different code from local. *Not verified by the challenge pass:* the named advisories (pdfminer CMap pickle CVE-2025-64512; requests CVE-2024-35195 / CVE-2024-47081; urllib3 < 2.6 decompression advisories). pdfminer is the parser B9 runs on attacker-chosen PDFs.
*Fix:* re-pin to the verified versions and upgrade pdfplumber/pdfminer, requests and urllib3. Generate a hashed lock file. Send `Accept-Encoding: identity` in `safe_fetch`.

**A14 · PERF/ERROR · `src/jobs.py:41,150-183,276-279`, `web/deps.py:120` · V ×3 (web CORRECT, STRUCT, TEST)**
(a) There is one 4-thread pool for every user and job kind, with no per-user limit, so a few long searches queue every summary for minutes. (b) A job cancelled before it starts (every redeploy with a full pool) returns before `work()`, so its reserved owner-usage slot and DB connection are never released. The user loses a slot for 24 hours.
*Fix:* separate pools or a per-user limit (one search at a time). Release the slot via `on_finish` when the provider was never called. Add a shutdown test.

**T1 · TEST · `src/user_store.py:163-191,323-331` · ×1 (not challenged)**
The owner-key cap's 24-hour window is computed in two places (enforcing and displaying), and neither is tested at the window edge. Every test writes rows "now". The docstring points to a test file that does not exist.
*Fix:* backdated-row tests at 23h59m and 24h01m. One shared `since` helper.

**T2 · TEST · `tests/test_tokens.py:246-273`, `src/db.py:406-479` · ×1 (not challenged)**
Only the papers N1 rebuild is tested against an old schema. The `ALTER TABLE ADD COLUMN` paths for users, usage_events and summaries, and the `lower(login_name)` unique index, never run against a pre-migration table. The idempotence test opens a fresh DB, so its assertion cannot fail.
*Fix:* add a fixture with the 2026-09-15 schema, populated, including two case-differing names.

**S1 · ARCH · `web/routes_summaries.py:201-339` vs `agents/summarization_agent.py:167-257` · ×2 (STRUCT, LLM CORRECT; the double-send part is V as A13)**
"Summarize one paper" is written twice and the copies have drifted. Abstract recovery exists only on the web side, and so does the retry of abstract-only summaries. `summary_text` follows different conventions, so CLI summaries are sent twice in review prompts (`src/review.py:53-60`, verified). Both import the private `_coerce_summary`.
*Fix:* one `src/summarize.py` function used by both, with spend accounting wrapped around it in the route only.

**S2 · ARCH · `web/routes_searches.py:42-125`, `agents/monitor.py:114-146`, `web/routes_discover.py:58-85` · ×2 (partly V as B13)**
The preconditions for running a filter are re-implemented per entry point. `normalise_filter` runs only on the web path (latent: no current filter has a legacy shape). The empty-filter refusal is written twice, and the discover route skips it (whitespace description, P10). Source failures reach callers as formatted strings, which are parsed back with two parsers that import the private `_SOURCE_LABELS`.
*Fix:* `orchestrator.search` normalises, refuses empty filters, and reports failures through a structured callback.

**S3 · ARCH · `src/filtering.py:154-164`, `src/sources/query_builder.py:66-91`, `web/static/app.js:14-27` · ×2**
The filter vocabulary (categories, paper types, version and published labels, licenses, species, organism list) is maintained by hand in three places. Display labels double as stored values. An unrecognised value falls through as "no restriction". This is editorial content in code (rule 9) and the root cause of B5.
*Fix:* `filter_vocabulary.yaml` with ids, served to the client.

**S4 · ARCH · `agents/search_agent.py`, `run.sh:107`, `key_terms.json` · V (as B12)**
`./run.sh search` still runs the legacy bioRxiv-only agent. Every `key_terms.json` profile has empty keywords, so it stores every paper in the category without the empty-filter guard. It fetches only the first page, and `except Exception: return 0, 0` still reports success.
*Fix:* delete it and point `run.sh search` at `monitor.py --all`. *Question for the author:* is it still used?

### Minor

Web backend
- **M1** `web/routes_reviews.py:145-146`: missing credentials return a bare 500. Map to 400/503 as the sibling routes do. `:159-162` returns 404 "expired"/"unknown" where the siblings return 410 with a sentence. The review worker raises `HTTPException`, so the user sees "HTTPException: 400: …".
- **M2** `web/routes_discover.py:58-85`: a whitespace-only description runs an unfiltered search plus a model call (P10). Discover also enriches up to ~60 papers whose enrichment nothing reads (P11). Pass `enrich_only=lambda r: False`.
- **M3** `web/routes_session.py:308-310`: saving a key for a new provider with a blank model keeps the old provider's `preferred_model`.
- **M4** `web/routes_session.py:223-252`, `src/accounts.py:97-103`: no rate limit on sign-in, lookup or recover. Each bad attempt runs a 16 MB scrypt, so a flood can exhaust the thread pool and memory. The TODO is at `src/access_codes.py:41-42`.
- **M5** `src/access_codes.py:652-655`: after `reset-pin`, the first person to present the code sets the PIN and inherits the stored LLM key. `/api/session/lookup` reveals `pin_set: false`.
- **M6** `web/routes_session.py:273-276`: sign-out does not rotate the session nonce, so copied cookies stay valid for 30 days.
- **M7** `web/app.py:153-170`: the public `/healthz` exposes `db_path`, provider and model, `owner_key_set` and `access_code_set`.
- **M8** `src/llm_providers.py:450-459` (V-partial): any non-empty user key with `provider: "ollama"` is recorded as `key_source="user"` and lets the user choose any model on the owner's Ollama. It gives no extra uncapped access, because keyless Ollama is already uncapped.
- **M9** `src/llm_providers.py:451`: a user key with an empty provider is assumed to belong to the server default and can be sent to the wrong vendor.
- **M10** `web/auth.py:132-184`: `current_user` is `async def` doing blocking SQLite, stat calls and a YAML reload on the event loop. Make it plain `def`.
- **M11** `web/routes_searches.py:322-388`, `web/routes_references.py:190-243`, `web/routes_reviews.py:55-66`: an N+1 summary lookup (~6,000 queries for 2,000 results) exists in four hand-written copies.
- **M12** `web/routes_summaries.py:60-128` plus two siblings: spend admission and settlement are copied three times and have drifted. Move them to `src/spend.py`.

Front end
- **M13** `app.js:2509-2517`: "Summarize checked" does not mark rows busy, so a row click can double-bill a paper that is in the batch.
- **M14** `app.js:2048-2061,2684-2691`: in `selectRefList` and `reviewChecked`, list A's response can render under list B.
- **M15** `app.js:2809-2816`: the page buttons call `loadResults()` without catch, so an expired job fails silently and the offset drifts.
- **M16** `app.js:1826-1831`: one network blip stops the Discover poll for good while the job keeps running (P1).
- **M17** `app.js:2188-2199`: deleting a list leaves its review and status on screen.

Retrieval
- **M18** `src/sources/biorxiv_medrxiv.py:84,112-139`: the bioRxiv version is never passed on, so every record reports v1 and the version filter misbehaves.
- **M19** `src/sources/crossref.py:123`: `"container-title": []` raises IndexError, which is logged at DEBUG as a Crossref "failure".
- **M20** `src/sources/orchestrator.py:373-375`: `normalize()` failures are dropped at DEBUG and never counted (P2).
- **M21** `src/paper_meta.py:217-219,356-359`, `src/sources/europepmc.py:198-232`: outages at Europe PMC, PMC and OpenAlex return `None`/`""`, so the user is told "nothing found" rather than "try again" (P1). OpenAlex has no retry (P5).
- **M22** `src/sources/dedup.py:49-58`: the surname key differs by source format ("Smith J" gives "j"), so title-based dedup rarely matches across sources. arXiv records carry no DOI.
- **M23** `src/pdf_handler.py:88-111`: writes straight to the final path, so a partial download is later treated as complete (P6).
- **M24** `agents/monitor.py:194-197`, `src/pdf_handler.py:92-101`: CLI and desktop downloads bypass `safe_fetch` (no scheme or host check, no size cap, no `%PDF` check). *Question:* does monitor `--download-dir` run on the server?

LLM and data
- **M25** `agents/summarization_agent.py:286` (V-partial): `--mock --paper-id` writes canned findings into the real default DB as `source_text="full_text"` (the rows are tagged `model_version="mock"`).
- **M26** `src/db.py:720` via the CLI `--paper-id` path: an abstract stand-in can overwrite a full-text summary (the same fix as A1).
- **M27** `src/db.py:676-679`: every `IntegrityError` in `insert_paper`, including NOT NULL, is logged at DEBUG as "already exists".
- **M28** `agents/summarization_agent.py:197-201`: a NULL abstract crashes `.strip()`, and "None" is sent to Ollama.
- **M29** `src/llm.py:89-97`: the Ollama call has no retry, and a non-JSON body raises a bare `ValueError` (P5).
- **M30** `user_reviews` rows are orphaned when a list is deleted, because foreign keys are off.

Structure
- **M31** `src/sources/psyarxiv.py` / `socarxiv.py`: two ~200-line copies. The orchestrator dispatches on name lists in three places. `_SOURCE_TRUST` is unused. `openalex: enabled: true` has no adapter and does nothing, silently.
- **M32** `Dockerfile:23`, `web/routes_session.py:137-141`: `filters.json` is at once the owner's personal store, the monitor input and the new-user seed, so colleagues are seeded with the owner's topics and two empty "New Filter" entries.
- **M33** `src/llm.py:99-173`: the Ollama client returns `None` rather than raising, and it has no abstract budget and no prompt delimiters.
- **M34** Dead code: `src/selection.py` and `src/sources/cache.py`, the legacy `db.py` bookmark and search-history methods, and `run.sh test` → `test_components.py`, which does not exist. `run.sh` with no argument still launches the GUI and checks for Ollama.

Tests
- **M35** `tests/web/test_frontend_wiring.py:1134-1184` and ~50 more: the spend gates are asserted by substring checks on `app.js` source. Move them to the existing node harness.
- **M36** `tests/web/test_frontend_wiring.py:44-61`: the JS↔API contract check ignores the HTTP method.
- **M37** `.github/workflows/tests.yml`: CI tests only 3.12, while the README claims 3.9+, and `tests/conftest.py:51` (`dict | None`) will not import on 3.9.
- **M38** `tests/web/test_requirements.py:68-75`: `importorskip` turns a missing pinned dependency into a skip.
- **M39** `tests/web/test_summaries_routes.py:506`: asserts `>= 0` under a provider that never reserves a slot, so it checks nothing.

## Systemic patterns

1. **Guards that live only in the browser (P10), 6 instances.** The summary "keep full text" skip (A1), filter name collision (A2), billed-job de-dup (A7), the empty discover description (M2), batch/row busy state (M13), and the review preview's "nothing to review". Each is a server-side precondition written only in `app.js`. The fix class is: enforce in the route or store, and test from the route.
2. **Searches narrowed silently (P2/P3), 8 instances.** The source budget (B1), empty dates (B2), OSF first word (B3), wildcard (B4), dead facets (B5), bioRxiv dates and medRxiv (B7), normalize drops (M20), and dedup misses (M22). Every one returns a plausible, smaller result set with no status line. This is the class to add to `LEARNINGS.md`: *every stage that narrows the candidate set must be able to say what it removed.*
3. **Hand-written parallel implementations that drift.** Summarize (S1), filter-run preconditions (S2), vocabulary (S3), spend settlement (M12), summary cards (M11), OSF adapters (M31), the pollers (A6/M16, whose own comments cite past drift), and the Ollama client (M33). The drift has already caused concrete defects: double-sent review text, a 500 instead of a 400, and 404 instead of 410.
4. **Failure reported as success.** `insert_summary` returning `None` (A8), `search_agent` returning `(0,0)` + success (S4), `get_unsummarized_papers` always returning 0 with success (A11), `IntegrityError` treated as a duplicate (M27), outages reported as "not found" (M21).
5. **The CLI agents have rotted.** A11, A12, S4, M25, M28, and `run.sh test`. The web path has had the fixes, and the agents have not. Decide whether they are supported. If they are, route them through the same shared code as the web app. If not, delete them.
6. **Hostile input processed in-process without bounds.** Regex (B8), PDF parse (B9), the parser version (B10), and monitor downloads (M24). The SSRF layer is good, but nothing bounds CPU or memory after the fetch.

## Refuted in challenge pass

No finding was fully refuted. Parts refuted or downgraded:
- **A3, date part:** `newFilter` resets the range toggle (`app.js:1650`), and `buildFilterDict` reads the dates only when the toggle is on (`1626-1628`). Stale dates are not saved.
- **A9 → M25:** `--mock` writes to the real DB only via `--paper-id` with full text found. The default batch path finds 0 papers (A11), and the rows are tagged `model_version="mock"`.
- **B5, license part:** bioRxiv's raw `cc_by`-style values do match. The defect holds for the other sources and for Unpaywall-enriched licenses.
- **B11 → M8:** there is no cap bypass of any consequence. Keyless Ollama is already uncapped, and no owner credential is involved. What remains is a mislabelled `key_source` and a model choice the user controls.
- **B13:** confirmed but latent. No current filter has a legacy shape. It is folded into S2.
- **A6, main search:** the window is narrow, because Run stays disabled until `searchFinished`. The filter-test path remains fully exposed.
- **B10:** the version mismatch is verified. The advisory claims were not checked against an advisory database.

## Questions only the author can answer

1. Is the shared, one-per-paper summary meant to be writable by every invited user (A1)? The fix differs depending on whether it is: server-side re-resolution, or per-user summaries.
2. Are `agents/search_agent.py`, `agents/summarization_agent.py` and `run.sh` still supported (S4, A11, A12)? If not, deleting them removes a whole class of findings.
3. Does `monitor.py --download-dir` run on the deployed server or only on a Mac (M24)?
4. Should a published article and its preprint merge in dedup, and if they do, should the merged record keep `is_preprint` (M22)?
5. Should CLI spend on the owner key be metered? The CLI writes no `usage_events`.
6. Is Python 3.9 still a supported version (M37), or should the docs say 3.12?
