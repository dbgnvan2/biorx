# TODO

> Items from 2026-09-15 are being reconciled and worked through in
> `docs/implementation_plan_2026-09-16_backlog.md`; that plan is the current list.

## Europe PMC TITLE_ABS (`docs/implementation_plan_2026-10-07_title_abs.md`)

- The local filter matches a phrase as written, so "kin selection" does not
  match "kin-selection"; Europe PMC does. Found in the TA3 live check (4 of 5
  kept). Treating hyphens as spaces in phrase matching would close it.

## Search-within gate (`docs/cycles/2026-10-07_search-within-qa-gate.md`) — APPROVED

- L1, L2, L3, L5 fixed in the same batch. L4 not fixed: title and abstract
  are joined with a space before matching, so a phrase can match across the
  join ("…cortisol" + "Sleep…" = "cortisol sleep"). Same as the filter's
  Title-or-abstract box has always done; fix both together if it matters.

## AND terms gate (`docs/cycles/2026-10-07_and-terms-qa-gate.md`) — APPROVED

- `_lucene_term` escapes only `"`; a part such as `OR`, `NOT`, or one with
  `:` `(` `)` reaches Europe PMC as query syntax while the local filter reads
  it literally. Pre-existing; Lucene-escape reserved words and characters in
  the next query-builder change.
- `src/search_terms.is_and_term` has no callers; delete it or use it.
- arXiv has no wildcards: `cooperati*` is sent as `cooperati` and finds 0 there.

## From the browser run (`docs/cycles/2026-09-29_browser-run.md`)

- All findings fixed (see the report). A search including bioRxiv/medRxiv
  takes about 3 minutes for two weeks — every paper in the window is read.
  **Accepted by the owner 2026-09-30 (decision D0).** Revisit only if it gets in
  the way: cache the window per date range (plan option C) rather than lower
  `max_pages`, which would read less of the window.

## CI race (`docs/cycles/2026-09-28_review-ci-race-qa-gate.md`) — APPROVED

- CI was red on and off from batch 2 to batch 7: a test returned while its
  summary job ran on, the stub was undone, and the job reached the network.
  Fixed in 7805ac4 (`settle_jobs` in tests/web/conftest.py). A red CI run
  should have stopped the push each time; from here on, check CI after a push.

## From the review batch-8 gates (`docs/cycles/2026-09-28_review-batch8-*qa-gate.md`) — APPROVED

- Both low findings answered in 0bf9ddb (suite count in the report; name
  folding matches each comparison — login names as SQLite's lower(), filter
  names with casefold).
- The browser run is done (`docs/cycles/2026-09-29_browser-run.md`).
- **Moot 2026-09-30: gui.py retired (decision D1).** `gui.py` still imports modules whose interfaces changed in this review; it
  is not updated (retiring).

## From the review batch-7 gates (`docs/cycles/2026-09-28_review-batch7-*qa-gate.md`) — APPROVED

- Finding 1 (removing a key kept the old model) is fixed in e8c5dca.
- M4 behind Railway's proxy: **checked 2026-09-29.** `TRUST_PROXY=1` is set;
  the limit is per client address and a made-up X-Forwarded-For does not get
  around it (details in `docs/spec_coverage_review_fixes.md`).
- **Fixed 2026-09-30 (plan T1.5): slot before token, one-transaction reset, guarded settings.** Low-severity notes, not fixed: the rate limiter spends a token before the
  PIN-check slot is taken; `reset_pin` is not one transaction; `from_config`
  does not guard `int()` on the `sign_in:` values.

## From the review batch-6 gate (`docs/cycles/2026-09-28_review-batch6-qa-gate.md`) — APPROVED

- Finding 1 (request-thread connection kept between requests on routes other
  than summaries/Discover/reviews): **left as is, deliberately.** It is one
  idle connection per pooled worker thread (no transaction, no lock), closed
  when the thread exits. A yield dependency cannot release it: its teardown can
  run on a different thread from the handler, and the connection is
  thread-local. The per-route decorator stays on the billed routes.
- Finding 2 (the 512 KB scrape cap is a constant and cuts silently): fixed in
  review batch 8 — the cap is in `sources_config.yaml` and a cut is logged.
- Finding 3 (the Dockerfile's "regenerate with" comment omits flags the lock
  header records): fixed in review batch 8.
- Finding 4 (a server that gzips a PDF despite `Accept-Encoding: identity`
  gives NotAPdf): recorded. Decoding would reopen the decompression-bomb
  exposure; revisit only if such a server is seen.

## From the review batch-5 gate (`docs/cycles/2026-09-28_review-batch5-qa-gate.md`) — APPROVED

- Both findings are fixed in review batch 6: the request connection is released
  on the admission-failure and review-409 paths too, and a reuse job that finds
  no stored summary fails with a plain message instead of an AttributeError.

## From the review batch-4 gate (`docs/cycles/2026-09-28_review-batch4-qa-gate.md`) — APPROVED

- Both notes are fixed in review batch 5: at the cap limit a duplicate run got
  429 instead of 409, and a free reuse of a stored summary was refused (the slot
  was reserved before those checks); the request's database connection was not
  released on the success path.

## From the review batch-3 gate (`docs/cycles/2026-09-28_review-batch3-qa-gate.md`) — APPROVED, deferred

- LOW / docs — `APP_SPEC.md`, `BUILD_SUMMARY.md`, `PROJECT_COMPLETE.md` and
  `GEMINI.md` still name `agents/search_agent.py`. Folded into batch 8 (M34).
- LOW / P2 — `seed_filters_from_file` still upserts, so two seed entries with the
  same name collapse into one for a new account. M32 (batch 8) replaces the seed.
- LOW — the filter-name clash check uses SQLite `lower()`, which folds ASCII only;
  "CAFÉ" and "café" can both be saved.

## From the review batch-2 gates (`docs/cycles/2026-09-28_review-batch2-*qa-gate.md`) — APPROVED, deferred

- **Moot 2026-09-30: gui.py retired (D1).** Known GUI effect (retiring app, not fixed): `gui.py` `DiscoverTermsWorker`
  calls `client.generate()` unguarded. Ollama now raises on failure, as the
  hosted providers always did, so a failed call there ends the worker without
  a message. Gate finding 2.

## From the review batch-1 gate (`docs/cycles/2026-09-28_review-batch1-qa-gate.md`) — APPROVED, deferred

- LOW / perf — `filter_papers` normalises the filter on every call, and the
  enrichment gate calls it once per record. Fold into M11 (batch 4 of
  `docs/implementation_plan_2026-09-28_review_fixes.md`).
- LOW / P19 — two source-label maps: `config.SOURCE_LABELS` ("bioRxiv / medRxiv")
  and `orchestrator._SOURCE_LABELS` ("bioRxiv/medRxiv") now both feed messages.
  S2 (batch 3) makes one public `source_label()`.
- **Fixed 2026-09-30 (plan T1.1): a display name keeps the lower-case particles before its last word, so "Ana da Silva" matches family "da Silva" and "Ana Silva" does not.** LOW / M22 residual — a multi-word surname ("da Silva") is "dasilva" from
  Europe PMC's lastName but "silva" from arXiv's last word, so those two copies
  still do not merge by title.
- **Moot 2026-09-30: gui.py retired (D1).** Known GUI effect (retiring app, not fixed): the desktop app's institution
  field is now ignored by the shared filter code.

## From the UI-enhancements gate (`docs/cycles/2026-09-19_ui-enhancements-qa-gate.md`) — APPROVED, deferred

- MED / P32 — `test_e2_tab_bar_is_sticky` pins `#notice { top: 52px }` by reading
  the same `styles.css` it asserts on, so it proves the value did not drift, not
  that 52px actually clears the tab bar. If the bar's padding or font grows its
  rendered height past 52px, the "notice behind the bar" defect reproduces and
  the test stays green. A real-browser visual check is the only true fix.

## Found by the first real review run (2026-09-21) — not yet fixed

A review of six papers (one full-text summary, five abstracts) on DeepSeek.
Every quotation in the output was verbatim from its sources and every specific
fact traced to one; "Dutch" and "WASO" were faithful rewordings of "in the
Netherlands" and "wake after sleep onset". The two problems are in the cost
dialog, not the review:

- **The review estimate leaves out the model's reply.** The dialog said 3.0k
  tokens; the run used 3,926 (2,357 in, 1,569 out). `reviewEstimate` counts the
  prompt only, while the summary estimator adds `completion_tokens_low/high`. A
  synthesis writes a long reply, so this under-states by about 30%. The same
  sibling-drift class as the four gate rejections: output was accounted for on
  one path and not the other.
- **The review dialog shows no price when one is known.** It says "cost depends
  on your provider's rates" even with a DeepSeek rate configured, because
  `reviewEstimate` sets the dollar fields to null rather than pricing the
  measured prompt at the payer's rate. The summary dialog does price it.

Fix both together: give the review estimate a completion allowance from config
and price it through `tokens.rate_for`, as the summary estimate does.

## From the term-chips gate (`docs/cycles/2026-09-26_term-chips-append-qa-gate.md`) — APPROVED, deferred

No finding above low. These were recorded rather than fixed after approval, so
what was pushed is exactly what was reviewed.

- **A rapid second term click is dropped silently (P2).** `state.discoverSaving`
  returns with no message while a save is in flight. Pre-existing, but it now
  sits on the left-click path, which is the click-heavy one. Queue the term or
  say the click was ignored.
- **`saveFilterAs` still overwrites by name (P5 sibling).** It POSTs the name
  from the prompt with no free-name step, so "Save as…" onto an existing name
  replaces that filter — the overwrite the chip path now prevents. Also listed
  under the earlier gate.
- **`createFilterFromTerm` truncates a name at 80 characters** because it calls
  `freeFilterName` without a `maxLen`, while the server allows 200.
- **A comment in `appendedFilterName` misstates the server's behaviour.** It
  says the server "silently cuts names at 200 characters"; the route rejects a
  longer name with 422, and the `[:200]` truncation is reachable only from the
  seed path. The code's behaviour is right; the rationale is not.
- **`test_dc4_both_save_paths_keep_the_id_put_returns` is a substring match**,
  so `saved.id + 1` would pass it. A browser-driven click-click test is the
  stronger check.

## From the review-and-chips gate (`docs/cycles/2026-09-21_review-and-chips-qa-gate.md`) — APPROVED, deferred

The three findings were fixed. The re-sweep recorded three older problems in the
pollers that were not part of this batch. Left for the next round rather than
patched after approval, so what was pushed is exactly what was reviewed.

- **The single Summarize button never gives up on an unreachable server.**
  `startSummary` (web/static/app.js) counts network failures and announces after
  three, but has no `POLL_GIVE_UP` branch: the button stays disabled and the
  interval runs until a reload. Move it onto `pollJobUntilSettled`.
- **Discover treats a network blip as the end of the job.** `pollDiscover`
  clears the poll and re-enables the button on any error, including status 0.
  The server job is still running with its allowance slot reserved, so a
  re-click can run it twice. The opposite mistake to the one above (P1).
- **A comment in my own code is false.** `pollJobUntilSettled`'s docstring says
  its rules match `startSummary`'s "given up on at POLL_GIVE_UP". startSummary
  has no give-up. It becomes true once the first item is done; fix the two
  together.

## Found while building the Discover chips (2026-09-21) — flagged, not fixed

- **Save and Save as… can silently overwrite a filter.** `POST /api/filters` is an
  upsert on the name (`user_store.upsert_filter`: `ON CONFLICT(user_id, name) DO
  UPDATE`). Press + New, type the name of a filter you already have, press Save,
  and the existing filter is replaced with no warning. Save as… does the same.
  The Discover chips guard their own path by choosing a free name; the two
  editor buttons do not. The proper fix is a server-side refusal (409) for a
  create whose name is taken, which changes existing save semantics.
- **The notice banner shifts the page down the first time it appears.** It sits
  in the document flow, so the first chip click moves every chip below it ~40px.
  It never auto-hides, so this happens once per page, not on every click.
- **Right-click has no touch equivalent on iOS Safari**, which does not fire
  `contextmenu` on long-press. Android Chrome does. On an iPhone, adding a term
  as a second group means typing it into the editor.

## From the token-capture gate (`docs/cycles/2026-09-20_token-capture-qa-gate.md`)

Findings 1-4 were fixed in the same session (see the re-gate). One item is
deferred deliberately:

- Discover calls are logged and capped under `kind = "summary"` (`USAGE_KIND`
  in `web/routes_summaries.py`). They share the summary daily allowance, which
  is existing behaviour, but it means the usage log calls a discover run a
  summary. Giving discover its own kind would also take it out of the cap's
  count unless the cap query is changed at the same time — a deliberate change
  to what users are allowed to spend, not a side effect of token accounting.

## From the full-text gates (2026-09-19) — APPROVED, deferred

- **Fixed 2026-09-30 (plan T1.3): only Unpaywall's 401/403 reads as a settings problem.** G1 (LOW) — `src/fulltext.default_get_json` reports any 401/403 as a settings
  problem; only Unpaywall's is. An OpenAlex/Semantic Scholar 403 (quota) should
  read as temporary.
- **Fixed 2026-09-30 (plan T1.4): the page sends null and the server's config decides.** G2 (LOW) — if `/healthz` fails at page load, the page falls back to title
  search on and overrides an operator's `find_by_title: false`.
- G3 (LOW) — the Settings checkbox re-sync after `/healthz` loads has no test.
- F6 (INFO, deliberate) — a real PDF whose title appears after the first 10,000
  characters is rejected as "a different document"; the abstract is kept instead.
- Older summaries (before 2026-09-19) have no recorded source and show a plain
  ✓ Summary; Summarize returns them as stored.

## From the filter-run batch gate (`docs/cycles/2026-09-18_filter-run-batch-qa-gate.md`) — APPROVED, deferred

Carried to the next batch rather than fixed after approval (a post-approval fix
would ship code the gate never read).
- LOW — `normalise_filter` runs in `filter_papers` but not in the GUI/monitor
  query-builder path (`gui.py`, `agents/monitor.py`, `src/sources/query_builder.py:118`),
  so a hand-edited top-level `keywords` string still splits into letters in the
  *query* sent to sources there. Fix: normalise once at every entry point.
- LOW — `enrich_only` relies on record identity; the cross-source dedup-merge path
  (a later source merging into a matched record) has no test.
- INFO — `tests/conftest.py` clears `DEFAULT_LLM_PROVIDER` but not `LLM_PROVIDER`
  from the developer's shell.
- INFO — `_enrich`'s "failed for N of M" uses one denominator (records attempted)
  for both Crossref and Unpaywall.

Also open from this batch:
- Railway: if the service sets `LLM_PROVIDER=anthropic`, it still wins over the
  yaml default — set `DEFAULT_LLM_PROVIDER=deepseek` there (start-up log shows which won).
- **Moot 2026-09-30: gui.py retired (D1).** The desktop GUI was not driven live in this batch; its changes are covered by
  unit tests under the venv only (CI has no PyQt6).

## From personal access codes (2026-09-18) — adjacent issues found, not fixed

- **No per-IP rate limit** on `POST /api/session` or `POST /api/session/lookup`.
  PIN lockout is per account; codes are 60 bits, so guessing a code is not
  practical, but a limit is still defence in depth.
- **Done 2026-09-30 (decision D2): the name sign-in, the recover route, the recovery dialog and those functions are removed.** **End of the switch-over:** once `ACCESS_CODE` is removed everywhere, delete
  the old name sign-in, `POST /api/session/recover`, the recovery-code dialog,
  and `accounts.sign_in`/`create_account`/`recover` if nothing else uses them.
- **Skipped for now by the owner 2026-09-30 (decision D3).** Per-user spend is not visible in the web app (usage plan, not yet approved).
- **Lockout can be used to annoy:** anyone who knows a name (old way) or a
  code can keep that account locked by sending 5 wrong PINs every 15 minutes.
  The usual lockout trade-off; a per-IP limit (above) would narrow it.
- Frontend wiring tests check the source text of app.js, so they cannot catch
  behaviour bugs (reload loops, stuck busy state). A browser test (Playwright)
  would.
- **First Railway deploy is the first `docker build`** of this image, and
  `railway ssh` has not been tried against it (which user the shell runs as
  decides whether the app can read a codes file made from it).
- `account:` in the codes file is read only on a code's first use; later edits
  to it are ignored. `list` could flag an entry whose binding differs.

## From the web-parity /csdp review (2026-09-17) — adjacent issues found, not fixed

- **Any user can overwrite the shared summary for a DOI (security, pre-existing).**
  `POST /api/summaries` stores the client's paper dict and replaces the one
  summary row per paper (`_paper_row_id` + `insert_summary`). A user can send a
  real DOI with an invented abstract and change the summary every other user
  sees. Fix before wider sharing: summarize only from server-held paper data
  (look the paper up by DOI/canonical_id and ignore client text fields), or
  store summaries per user. Found by the cold sweep; outside the reviewed range.
- **Filter Test runs the saved filter, not the form.** Unsaved edits are not
  tested; the desktop tests the form. Spec asks for the stored filter — a
  design choice to revisit.
- **Preferred model is not tied to a provider**: switching provider sends the
  old provider's model name (loud provider error, not silent).
- **PDF download delivers few PDFs for Europe PMC papers.** `pdf_url()` returns
  `best_oa_url`, which for PMC records is the article web page; the proxy now
  says "No PDF available (web page)" (422) where it used to return the HTML as a
  PDF. Europe PMC's own `?pdf=render` returns 403 to automated clients. Needs a
  real PDF URL source (PMC OA service / Unpaywall `url_for_pdf`) before the
  References tab's "Download PDFs" is useful for these papers. Same root as the
  N2 note below.
- **`/healthz` is unauthenticated** and returns `db_path` and `startup_warnings`.
- **Static assets have no cache-busting**: after a deploy, browsers keep the old
  `app.js`/`styles.css` until a hard reload (seen during the live check).
- **Fixed 2026-09-30 (plan T2.2): values left out come from llm_config.yaml.** **`OllamaClient.generate` has a hardcoded 120 s timeout** (`src/llm.py`) and
  ignores the provider's `timeout` in `llm_config.yaml`; a slow local model
  (qwen3.5:4b on this Mac) times out on Discover Terms. Also P4.
- **Left as is 2026-09-30: goes with D3 (spend visibility), skipped for now.** **Discover shares the summary cap.** An owner-billed discover run takes a
  "summary" slot (settled correctly now). If discover needs its own allowance,
  add a kind and a cap.
- **"Use date range" checkbox** in the Search tab has the stacked layout the
  source pickers had.
- **Not needed (plan T2.6, 2026-09-30): a full-text summary is stored and reused, so its PDF is not fetched again; a re-download happens only after no PDF was found.** **Summaries no longer use the PDF cache on the web path.** The fix for cache
  poisoning fetches each PDF into a temp file; a repeat summary re-downloads.
  A cache keyed by a hash of the server-validated URL would restore reuse.
- **Institution as a list** from the earlier web build is joined with ", "
  on read; `filter_papers` treats that as one term, so a multi-institution
  legacy filter matches nothing. None are known to exist.

## From batch-H gates (2026-09-16) — deferred, in-loop fix threshold not met

- **Fixed 2026-09-30 (plan T3.4): openalex_user_agent() warns once without an address.** **paper_meta recovery paths emit no warning (P5/MEDIUM)** — `openalex_user_agent()`,
  `_europepmc()`, and `_crossref_abstract()` load sources_config but never warn when
  no contact email is found. The orchestrator's startup warning covers the GUI/web
  paths; this gap is only when these functions are called standalone (e.g. CLI
  summaries). Fix: add `get_contact_email()` check + log.warning in each. Deferred
  because the primary orchestrator warning already fires, and per-call warnings
  would be noisy in batch runs.

- **Fixed 2026-09-30 (plan T3.5): docs/ is scanned too, except docs/cycles/.** **`_root_md_files` scans root-level `.md` only** — `docs/` is intentionally
  excluded because `docs/cycles/` gate files quote personal addresses for audit.
  The latent risk is that a non-cycles file added to `docs/` could contain a
  real address and escape the scan. Gate F4 — low risk given the exclusion is
  documented in the test comment; revisit if `docs/` grows non-audit content.

## From the N2 gate (`docs/cycles/2026-09-16_n2-qa-gate.md`) — APPROVED at fix-loop 1

- **Network guard limits.** It cannot see network use from a subprocess (a
  fresh interpreter), from code that captured socket functions at import time,
  or from a library with its own resolver (dnspython, aiohttp). None of those is
  reachable in this repo today — its only network path is requests/urllib3 — but
  say so in the guard's docstring so nobody over-trusts it.
- **Fixed 2026-09-30 (plan T3.1): the comment says what the tuple really covers.** **`_OUR_BUGS` is broader than its name**: `ImportError` can also mean an
  optional dependency is missing rather than a defect in this code.
- **Fixed 2026-09-30 (plan T1.6): a summary skips a best_oa_url that is not a PDF path.** **Adjacent to N2**: `pdf_url()` falls back to `best_oa_url`, which for PMC is
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

- **Fixed 2026-09-30 (plan T3.2): names the uid that runs.** **F8 (low)** — `fatal()`'s second line always says "mount the volume writable
  by biorx", but the non-root branch fails under whatever uid the platform
  enforced. The first line names the real uid, so the hint merely misleads.
- **Fixed 2026-09-30 (plan T3.3): read with ast; found BIORX_PDF_FONT undocumented.** **F6 (low)** — the "derive env vars from the code" test misses variables read
  through indirection (`KEY_ENC_SECRET`, the provider keys, the model names), so
  its guarantee rests partly on a hand-kept list.
- **No Content-Security-Policy header.** The client sets text rather than
  markup and checks URL schemes, but a CSP would be defence in depth.
- **Fixed 2026-09-30 (plan T2.7): jobs.max_unfinished_per_user (10), answered 429.** **Unbounded job creation**: any signed-in user can queue jobs without limit.
  (User creation is now bounded: one account per personal access code.) The spend cap bounds money, not memory.

## From the chunk-4 QA gate (`docs/cycles/2026-09-15_chunk4-qa-gate.md`)

REJECTED, then APPROVED at fix-loop 1. The two blocking findings are fixed; three
low findings are carried:

- Job creation is unbounded (user creation is now one account per access code). The spend cap bounds money, not memory.
- The shared summary endpoint returns `created_by_user_id`, which tells one
  colleague who ran a summary. Fine among colleagues; note it before the
  audience widens.
- The constant-time access-code comparison is asserted by inspecting the
  function's bytecode names rather than by behaviour.
- A worker blocked with no timeout would delay interpreter exit by up to ~150s
  (bounded by the provider and PDF timeouts). A delay, not a hang.

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
- **Checked 2026-09-30 (plan T2.4): load_filters keeps a list and --all runs both; now tested.** **`filters.json` names are not unique** (two entries are currently called
  "New Filter"), and `monitor.load_filters()` keys by name, so `--all` silently
  drops one.
- **`dict | None` in `agents/monitor.py`** breaks the Python 3.9 floor that
  `CLAUDE.md` and the arXiv spec claim to support.
- **Fixed 2026-09-30 (plan T3.6): extra hits are filtered out on title/abstract; the note says their only cost.** **`all:` is broader than Europe PMC's bare term** — it also matches authors,
  comments and journal-ref. Defensible, but currently undocumented.

## Pre-existing, found while reading

- ~~`src/llm.py` — prompt f-string sent a `# Limit to first 3000 chars` comment
  to the model.~~ Fixed 2026-09-18 (220e724).
- ~~`src/llm.py` — silent 3000-character truncation.~~ Fixed 2026-09-18: the
  budget is `max_text_chars` from `llm_config.yaml`; the agent logs what it drops.
- **Fixed 2026-09-30 (plan T2.2): the constants are gone.** `src/llm.py` — `OLLAMA_MODEL` is still a model id in source, now `qwen3.5:4b`
  and used only by a bare `OllamaClient()`; every real path passes the config
  model. Remove when nothing constructs a bare client.
- ~~`/opt/homebrew/bin/pytest` skipped every GUI test.~~ Fixed 2026-09-18
  (dfc1d8b): `CLAUDE.md` names the venv; the run summary announces GUI skips.
