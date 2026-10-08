# Changelog

## 2026-10-08 — today's gate TODO items (TG1–TG5)

From `docs/implementation_plan_2026-10-08_gate_todos.md`.

### Fixed
- A blank, non-numeric or out-of-range number in `sources_config.yaml` used
  to make every search fail. Searches now use the default and log which
  setting was wrong. This covers:
  - the per-source limit and its maximum;
  - the PsyArXiv/SocArXiv title-word count;
  - the bioRxiv/medRxiv window settings.
- The limit summary used its own default for the PsyArXiv/SocArXiv
  title-word count. It now uses the adapter's.

### Changed
- All years names its start date: "Searched all years (1900-01-01 to …)".
- A test now fails if the built-in defaults differ from `sources_config.yaml`.

## 2026-10-08 — the title-or-abstract box also matches the authors' keywords (KW)

From `docs/implementation_plan_2026-10-08_author_keywords.md`.

### Changed
- "Words in title or abstract" is now "Words in title, abstract or
  keywords". This box and Search within these results now match:
  - for Europe PMC and PubMed, the authors' keywords;
  - for PsyArXiv and SocArXiv, the authors' tags.
  The separate Title and Abstract boxes are unchanged. PubMed's own
  Title/Abstract search works the same way. For `internal family systems`,
  Europe PMC as PubMed now finds 24, the same as PubMed (was 21).
- Saved filters find more papers (about +2% for `loneliness`, +8% for
  `cooperati*`).
- The paper detail view lists the paper's keywords.

### Fixed
- Only the first 10 keywords or tags were kept, and a paper found by two
  sources kept only the first source's keywords.

## 2026-10-08 — the dates searched are shown; an All years choice (DW, AY)

From `docs/implementation_plan_2026-10-08_date_window.md`.

### Added
- Each search says which dates it covered: "Searched 2016-10-10 to
  2026-10-08", or "Searched all years (to …)". It is shown in the progress line, the results
  heading, the "no papers matched" note and the Filters-tab Test line, with
  a note that dates are when a paper first appeared (wording in
  sources_config.yaml, `date_window.hint`). The monitor logs it.
- **All years** on the Search tab and in the Filters editor. It searches from
  `search.all_years_start` (sources_config.yaml, 1900-01-01) to today and
  hides Days back and the date boxes. Saved filters store `all_years: true`.

### Why
- `internal family systems`: PubMed found 24 papers over all years. The app
  searched at most 10 years (Days back stops at 3,650) and did not say so.

## 2026-10-08 — clearer "results are incomplete" summary; a limit per saved filter (WS, FL)

From `docs/implementation_plan_2026-10-08_limits_and_warnings.md`.

### Changed
- The run-on warning ("Europe PMC had more results than Max results allows …
  PubMed … PsyArXiv …") is replaced by a summary: a heading, the most useful
  next step, one row per source with how far over it was ("read 200 of 2,391
  matches") and what to do for that source (untick PubMed when Europe PMC is
  ticked; put a word in the Title box for PsyArXiv/SocArXiv; untick
  bioRxiv/medRxiv when it is refused; raise the limit or narrow the words),
  and the limit with where to change it. Wording is in sources_config.yaml
  (`limit_summary`).
- "Max results" is now "Papers read per source". A saved filter has its own
  (Filters editor); Run, Test and the monitor use it. The Search tab shows it
  beside Run. The ad hoc box keeps its own. Default and maximum are in
  sources_config.yaml (`search:`).

### Fixed
- Running a saved filter used the ad hoc box's limit without saying so; the
  filter Test button always used 200; the monitor ignored any per-filter value.


## 2026-10-07 — wildcard on a hyphenated word (HW1–HW3)

### Fixed
- `COVID-1*` was sent to Europe PMC as the exact word "COVID-1" (0 results).
  A wildcard word is now split where the app's own matching splits it
  (spaces and punctuation): `COVID-1*` → `COVID AND 1*`. Live, 30 days: 0 →
  1,229 Europe PMC hits, 195 of the first 200 kept by the filter.
  (`docs/implementation_plan_2026-10-07_hyphen_wildcard.md`)


## 2026-10-07 — today's gate follow-ups (TD1–TD11)

From `docs/implementation_plan_2026-10-07_gate_todos.md`.

### Fixed
- Punctuation and hyphens are matched as spaces, as Europe PMC matches them:
  "kin selection" matches "kin-selection", "COVID-19: outcomes" matches
  "COVID-19 outcomes" (live: 0 of 200 kept → 200 of 200).
- A phrase no longer matches across the end of the title and the start of the
  abstract; separate AND parts may still be in different fields.
- Terms containing query syntax (`:`, brackets, a part that is just `OR`) are
  quoted for Europe PMC and arXiv, so they are searched as words. A phrase
  ending in `*` is sent as its words with the wildcard on the last.
- arXiv gets the wildcard instead of a stripped non-word (`cooperati*`: 50
  results instead of 0); inside an AND term, wildcard parts are left out of
  the arXiv query when another part can narrow it.
- The bioRxiv/medRxiv `servers:` setting is read (it was looked for in the
  wrong place).
- Each search fixes its date window once, so every source and the
  bioRxiv/medRxiv note use the same dates.
- A source re-sending its own paper counts as a repeat even after another
  source's copy merged with it.


## 2026-10-07 — sources whose papers were already found say so (DS1–DS4)

From `docs/implementation_plan_2026-10-07_duplicate_status.md`.

### Fixed
- With Europe PMC and PubMed both ticked, PubMed's line read "0 fetched", as
  if it had been skipped: Europe PMC includes PubMed, so every PubMed paper was
  already found and merged. It now reads "PubMed: 52 papers read, all already
  found by an earlier source" (or "3 new, 49 already found"). Unchanged when
  there are no duplicates.


## 2026-10-07 — bioRxiv/medRxiv read directly only where it helps (BW1–BW7)

From `docs/implementation_plan_2026-10-07_biorxiv_window.md`.

### Fixed
- bioRxiv's API cannot search words and lists papers oldest first. A long
  range (e.g. 2019–2026, 589,593 papers) read the first weeks of 2019 for 3–4
  minutes and stopped at the page limit. Now a range ending more than 60 days
  ago is not read directly at all, and a longer recent range reads only its
  last 21 days directly (the newest preprints, which Europe PMC may not have
  yet). Europe PMC indexes bioRxiv and medRxiv preprints with word search.
- The search page says which of these happened, under the progress line,
  and asks you to tick Europe PMC if it is not selected. A skipped range is
  not reported as "page limit reached".

### Changed
- `sources_config.yaml`: `biorxiv_medrxiv.max_direct_days: 21`,
  `europepmc_lag_days: 60`.


## 2026-10-07 — Europe PMC searches title and abstract, not full text (TA1–TA6)

From `docs/implementation_plan_2026-10-07_title_abs.md`.

### Fixed
- Title-or-abstract terms were sent to Europe PMC and PubMed as bare words,
  which also match full text. The app keeps only title/abstract matches, so
  most records read (up to Max results) were thrown away and real matches past
  that limit were never read. They are now sent as `TITLE_ABS:`. Live, first
  200 records: `loneliness` (14 days) 94 of 200 kept → 94 of 94;
  `cooperati* AND survival` (14 days) 16 of 200 → 17 of 17 (one paper had
  been missed).
- The Discover term check uses the same query, so its counts are now
  title/abstract counts, matching what a filter shows; the wording says so.


## 2026-10-07 — search within a search's results (SW1–SW8)

From `docs/implementation_plan_2026-10-07_search_within.md`.

### Added
- A "Search within these results" box above the results. Each term added
  becomes a chip; chips combine with AND, so the shown results are
  {the search} AND {term 1} AND {term 2}. Same syntax as every box (commas,
  AND, `*`), matched against title or abstract. Only the results the search
  returned are searched; no source is asked again.
- "Save all N results", the summaries list and the summaries PDF use the
  narrowed results. Changing the terms clears ticked papers (with a notice);
  a new search clears the terms.


## 2026-10-07 — "all of these words" in one search box (AND1–AND8)

From `docs/implementation_plan_2026-10-07_and_terms.md`.

### Added
- Uppercase `AND` inside a term means every part must appear:
  `cooperati* AND survival` finds papers with both words, in any order.
  Commas still mean "any of these" (`a AND b, c` = (a and b) or c), and
  lowercase "and" is still part of a phrase. Works in the Search box and in
  every Title / Abstract / Title-or-abstract box, for Europe PMC, PubMed,
  arXiv, PsyArXiv, SocArXiv, OSF and bioRxiv/medRxiv, and in the Discover counts.
- A one-line hint under the Search box and the filter's text groups.

### Changed
- A saved filter with ` AND ` in a term used to search that whole term as an
  exact phrase (and found nothing); it now requires each part. Locally this
  affects one filter, "Bowen and Marriage" (title and abstract
  `Bowen AND Marriage`). Filters on production were not checked from here.


## 2026-10-07 — Discover offers only terms that find papers (DT9)

From `docs/implementation_plan_2026-10-07_discover_terms_verified.md`.

### Fixed
- Discover could still offer a term that finds nothing (e.g. "cooperative
  species survival": 0 papers in Europe PMC). Each suggested term is now
  searched in Europe PMC with the query a filter made from it sends, over the
  same window. Terms that find no papers are sent back to the model once for
  replacements; terms that still find none are not offered and are listed
  under the terms ("Not offered (found no papers): …").

### Changed
- A term button shows what a search for it finds ("1,147 in Europe PMC");
  the count in the sampled papers moved to its tooltip. If Europe PMC does
  not answer, the term is kept and marked "not checked".
- Settings `discover.replace_prompt`, `check_timeout_s`, `check_delay_s` in
  `llm_config.yaml`. At most one extra model call per run, billed with the first.


## 2026-10-07 — Discover Terms suggest terms that find papers

From `docs/implementation_plan_2026-10-07_discover_terms.md` (DT6–DT8).

### Fixed
- A filter made by clicking a suggested term searched the last 7 days, while
  the terms came from papers in the last 90 (`discover.days_back`). It now
  uses the same window. Adding a term to a filter that is already open leaves
  its window alone, and the message says when that window is shorter.
- The model was asked for 2–4 word phrases. A term with a space is searched
  as an exact phrase, so most found nothing. It is now asked for 1–3 word
  terms that appear word for word in the papers it was given.

### Added
- Each suggested term shows how many of the sampled papers contain it
  ("12 of 30"), counting whole words, so "aging" does not count a paper about
  imaging. Terms found in none are shown dimmed with a dashed border and a
  warning, not hidden. A line under the terms gives the total.
- The Discover instructions are in `llm_config.yaml` (`discover.system_prompt`);
  paper text is sent inside `<papers>` tags, apart from the instructions.

## 2026-09-30 — one front end, one way to sign in

### Removed
- **The PyQt6 desktop app** (`gui.py`, `run_gui.sh`, and `src/selection.py`,
  which only it used), with its tests and the PyQt6 dependency. The web app
  and the command-line tools (`agents/monitor.py`,
  `agents/summarization_agent.py`) are what remain. The desktop app's saved
  reference lists stay in the database, unread.
- **The old sign-in** with the shared `ACCESS_CODE` + name + PIN, its "Forgot
  PIN?" recovery codes, `POST /api/session/recover`, and the "Sign in with
  name (old way)" link. Everyone signs in with a personal access code + PIN;
  a forgotten PIN is reset by the owner (`python -m src.access_codes
  reset-pin`). `ACCESS_CODE` is no longer read. On production all three
  accounts already used codes.

## 2026-09-30 — the next-steps plan (tiers 1–3)

From `docs/implementation_plan_2026-09-29_next.md`; status of every item is at
the top of that file.

### Fixed
- Papers stored as " (PubMed)" (a PubMed paper without a journal, saved
  before the label fix) now read "PubMed"; stray spaces around stored labels
  are trimmed when the app starts.
- The same paper from Europe PMC ("da Silva") and arXiv ("Ana da Silva") is
  now recognised as one.
- An OpenAlex or Semantic Scholar refusal reads as temporary; only
  Unpaywall's means a setting is wrong.
- If the page cannot read the server's settings, title search follows the
  server's own setting instead of switching on.
- Sign-in: a "server busy" answer no longer uses up one of your attempts; a
  PIN reset is all-or-nothing; a typo in `sign_in:` settings no longer stops
  the server starting.
- A summary no longer downloads a paper's web page as if it were its PDF.
- One user can have at most 10 jobs waiting or running
  (`llm_config.yaml` `jobs.max_unfinished_per_user`); more are refused with
  a message that is not mistaken for the daily allowance.
- Unpaywall lookups and PDF downloads send the contact address like every
  other request; abstract lookups warn once when none is set.
- `BIORX_PDF_FONT` is now in `.env.example`; the check that lists
  environment variables reads the code properly and found it.
- The container's start-up error names the user that must be able to write
  the volume.
- `.claude/launch.json`, `.claude/settings.local.json` and
  `.test-qa-report.md` are ignored by git (the launch file holds local test
  secrets).

## 2026-09-29 — checked on production

### Fixed
- A bioRxiv/medRxiv search's counts now add up. On production, "inflammation"
  over 14 days read 5,095 papers: 149 matched, 4,924 did not, and 22 were not
  mentioned. Matches that repeat a paper already read (for example another
  version of it) were merged without being counted; the status line now says
  how many, and the log warns if the numbers ever do not add up.
- When a site refuses a PDF download (HTTP 403/429), the summary now names
  the site ("www.biorxiv.org refused the download (HTTP 429)") instead of
  "The host answered 429.", which read as "try again later". bioRxiv and
  medRxiv refuse every request from the server's host (Railway), so their
  papers cannot get a full-text summary there; the summary says so once and
  points to running the app on your own computer. The wording is in
  `sources_config.yaml` (`full_text.refused_download_notes`).
- A download that is redirected to an `http://` address is now asked for as
  `https://`. doi.org sends many DOIs to plain-http addresses
  (`http://biorxiv.org/lookup/doi/…`), so a free copy found through a DOI link
  was refused with "URL must use https". The address checks on every hop are
  unchanged; other schemes (ftp, file) are still refused.
- On a narrow screen, a results table made the whole page scroll sideways
  (385 px wide page, 586 px of content). Tables now scroll inside their card.

## 2026-09-29 — bioRxiv reads the whole window; smaller fixes from the browser run

### Changed
- **bioRxiv and medRxiv: Max results now counts matching papers.** Their API
  cannot search words, so every paper in the date range is read (up to a page
  limit, `sources_config.yaml`) and the filter is applied here. Two weeks is
  about 4,900 papers and takes about 3 minutes; "inflammation" found 144
  where it found 1 before. A range longer than the limit is cut off and the
  search says so.

### Fixed
- "Exclude animal studies" and "Human studies only" also drop papers whose
  title names an animal study (mouse, rat, zebrafish, "porcine model", …),
  for every source. The terms are in `filter_vocabulary.yaml`. Words with
  other meanings in human research ("bovine serum albumin", "Chinese hamster
  ovary cells", "mouse tracking") do not count, so some veterinary titles
  ("Bovine mastitis") still get through.
- Reviews show headings, lists, bold and italics instead of `**…**`.
- PubMed titles no longer show `<i>…</i>`; titles and abstracts stored before
  the fix are cleaned when the app starts.
- Summaries say "no key needed" / "your key" / "shared key"; reference lists
  show "bioRxiv" instead of `biorxiv_medrxiv`; PubMed papers without a journal
  read "PubMed".

## 2026-09-29 — fixes from the browser run

A run through the common flows in a browser, with screenshots:
`docs/cycles/2026-09-29_browser-run.md`.

### Fixed
- **bioRxiv and medRxiv searches read about 1% of the papers in the date
  range, without saying so.** Their API now sends 30 papers at a time and the
  search stopped after the first batch. It now reads on until the Max results
  limit, and says when that limit cut it off.
- **Summaries with the local model (Ollama, qwen3.5) always failed** with
  "empty response". Thinking is now turned off for it (`llm_config.yaml`).
- **Europe PMC abstracts showed HTML tags** such as `<h4>Objective</h4>`.
  They now read as text ("Objective: …"). Crossref abstracts use the same
  rules, which no longer drop text between a "<" and a ">" in the abstract.
- **An API key for Ollama was kept in the browser** after the server refused
  it, and every summary then failed. The page now refuses it before saving,
  and choosing Ollama clears a key kept in the browser.

## 2026-09-28 — tests that can fail, foreign keys on, dead code out

Review fixes, batch 8 (the last). Gates: `docs/cycles/2026-09-28_review-batch8-qa-gate.md`
and `docs/cycles/2026-09-28_review-batch8-regate-qa-gate.md` (APPROVED).
Status of every plan item: `docs/spec_coverage_review_fixes.md`.

### Fixed
- **A double click on "Summarize checked" or "Review checked" could bill a
  paper twice.** Both now claim the run before anything else happens.
- **Deleting a reference list removes its reviews.** Foreign keys are now
  enforced; a startup migration removes reviews and list items whose list was
  already deleted, and logs how many.
- **An old database with login names that differ only in case opens** instead
  of failing; the newer account is renamed "name (2)" and logged.
- `monitor.py` says why a source was not fully searched ("truncated",
  "unavailable", …) instead of calling every such source "failed".
- The abstract scraper's page limit is in `sources_config.yaml`
  (`full_text.scrape_max_chars`), and a cut page is logged.
- Saved-filter names that differ only in case count as the same name, accented
  letters included ("CAFÉ"/"café").

### Changed
- **New web accounts start from `filters.seed.json`.** `filters.json` is your
  own local file for `monitor.py`: it is no longer in git or the Docker image.
  To start one: `cp filters.seed.json filters.json`.
- The allowance shown and the allowance enforced use one 24-hour window.
- Removed unused code: `src/sources/cache.py` (and `BIORX_CACHE_PATH`), the
  desktop bookmark methods, `upsert_filter`. Old design documents are marked
  as historical.
- README and CLAUDE.md say Python 3.12, the only version tested.

## 2026-09-28 — sign-in is harder to abuse

Review fixes, batch 7. Gates: `docs/cycles/2026-09-28_review-batch7-qa-gate.md`
and `docs/cycles/2026-09-28_review-batch7-regate-qa-gate.md` (APPROVED).

### Fixed
- **Sign-in attempts are rate-limited per address** (20 a minute, set in
  `llm_config.yaml` under `sign_in:`), and only a few PIN checks run at once;
  past that the server answers "try again shortly". Behind a proxy set
  `TRUST_PROXY=1` so the limit uses the real client address.
- **A PIN reset needs a one-time setup code**, printed by the reset command
  for the owner to pass on. The access code alone can no longer set a new PIN
  on a reset account. The reset also removes that account's saved API key.
- **Signing out ends that session everywhere**: a copy of the cookie taken
  before sign-out no longer works.
- **`/healthz` says only that the app is up.** The sign-in page reads
  `/api/gate`; the rest of the configuration needs a signed-in user
  (`/api/config`).
- An API key for a provider that takes none, or a key with no provider, is
  refused. Changing provider without choosing a model, or removing your key,
  clears the old model, so it is not sent to the new provider.

## 2026-09-28 — hostile pages and PDFs are bounded; dependencies patched

Review fixes, batch 6. Gate: `docs/cycles/2026-09-28_review-batch6-qa-gate.md` (APPROVED).

### Fixed
- **A large publisher page no longer stalls abstract recovery.** Abstracts are
  read by a linear scanner from at most the first 512 KB of a page
  (Python 3.12's html.parser took 67 s on a 320 KB page).
- **PDF text extraction runs in a separate process** with a page limit, a
  character limit, a time limit and (on Linux) a memory limit, all set in
  `sources_config.yaml` under `full_text:`. A PDF over a limit is reported as
  having no usable text, not as a crash.
- **`monitor.py --download-dir` downloads through the same guarded fetch** as
  the web app (no private addresses, size cap, PDF check), and a failed
  download no longer leaves a partial file under the final name. The desktop
  PDF handler does the same.
- **Dependencies are pinned and installed from a hashed lock**
  (`requirements-web.lock`, `pip install --require-hashes` in the image).
  pdfminer.six, cryptography, python-dotenv, requests and urllib3 move to
  patched versions; `pip-audit` on the lock reports no known vulnerabilities.
  Test tools (pytest, httpx) are in `requirements-test.txt`, not the image.
- The summary, Discover and review routes release their database connection
  on every exit, and a reused summary that disappears mid-run fails with a
  plain message.

## 2026-09-28 — the page shows and saves what is really there

Review fixes, batch 5. Gate: `docs/cycles/2026-09-28_review-batch5-qa-gate.md` (APPROVED).

### Fixed
- **A new filter starts clean.** "+ New" (and a term click) no longer carries
  over the last filter's category, paper type, licence or species.
- **Coming back to the Filters tab keeps the open filter's sources.** They were
  replaced with your defaults on the next save.
- **An API key saved in this browser belongs to your account.** It is removed
  when you sign out, and someone who signs in after you on the same browser
  cannot use it. (A key saved before this update has to be entered again.)
- **A slow reply can no longer end the wrong search.** A late answer about an
  earlier search or filter test is ignored instead of marking the new one
  "done" with no results; requests that never answer give up after 30 s; the
  Test button is disabled while a test runs.
- **After a reload, summaries still running show as busy**, and clicking
  Summarize on one follows the running job instead of paying for a second.
- **Summarize checked** marks its papers busy, so a row click cannot summarize
  one twice.
- A list's late reply or finished review is no longer drawn under another
  list; a page that fails to load says so; one missed Discover check no longer
  ends Discover; deleting a list clears its review.
- **Save / Save as refuse a name another filter already has.**
- At the daily limit, a second click on a running summary says "already
  running", and a paper with a stored summary still gets it (both used to say
  the limit was reached).

## 2026-09-28 — background work: one spend path, separate queues, no double runs

Review fixes, batch 4. Gate: `docs/cycles/2026-09-28_review-batch4-qa-gate.md` (APPROVED).

### Fixed
- **Summaries no longer wait behind searches.** Searches and model calls run in
  separate worker pools (sizes in `llm_config.yaml` under `jobs:`), and each
  person runs one search at a time.
- **A second click, a second tab or a reload cannot start a second paid run** of
  the same paper or the same list review: the server answers with the run
  already going.
- **A summary cancelled before it started** — every server restart cancels
  queued work — gives its day's allowance slot back instead of keeping it for
  24 hours.
- **Review errors read like the others:** no key gives a plain 400 (it was a
  bare "Internal Server Error"), an expired review says "run it again", and
  "nothing to review" no longer shows as "HTTPException: 400".
- **Lists and search summaries load faster:** a 2000-result search looks its
  summaries up in 9 database queries instead of about 6000.

### Changed
- Spend accounting (who pays, the daily allowance, what a call cost) lives in
  one module, `src/spend.py`, used by summaries, discover and reviews.

## 2026-09-28 — filters: no silent overwrites, one set of run rules

Review fixes, batch 3. Gate: `docs/cycles/2026-09-28_review-batch3-qa-gate.md` (APPROVED).

### Fixed
- **Saving or renaming a filter onto another filter's name is refused** ("A
  filter called … already exists"), whatever the letter case. It used to
  replace the other filter's contents and delete the one being edited, and say
  it had saved. A rename now keeps the filter's id.
- **Discover refuses a blank description** before it uses any of your daily
  allowance, and no longer looks up extra details for papers it only reads the
  titles of.

### Changed
- **Every way of running a filter follows the same rules**: the search engine
  itself reads older filter shapes and refuses a filter with nothing to search
  for, instead of each front end doing it (or not). Failed sources are passed to
  the web app and `monitor.py` as data, so changing a status message's wording
  can no longer stop failures being counted. Source names are spelled the same
  everywhere ("bioRxiv / medRxiv").
- **The old bioRxiv-only search agent and `key_terms.json` are gone.** It saved
  every paper in a category (its profiles had no terms) and reported failures
  as success. `./run.sh search` now runs `agents/monitor.py --all`; `./run.sh`
  on its own prints the commands.

## 2026-09-28 — summaries: checked on the server, one pipeline

Review fixes, batch 2. Gates: `docs/cycles/2026-09-28_review-batch2-qa-gate.md`
(REJECTED, one blocking finding) and `docs/cycles/2026-09-28_review-batch2-regate-qa-gate.md`
(APPROVED after fix loop 1).

### Fixed
- **Nobody can change another user's summary.** Summaries are shared, one per
  paper. The server used to store the title, abstract and PDF link the page
  sent; now it takes only the paper's DOI or id and looks the paper up itself —
  in the database, in your own recent search results, or at the source (arXiv,
  Europe PMC, Crossref). If the source cannot be reached you are told to try
  again, and nothing is stored.
- **A paid summary is never replaced by an abstract.** A paper that already has
  a full-text summary gets it back at once, with no model call and no charge.
- **A summary that could not be saved says so** ("shown but could not be saved
  — running it again will call the model again") instead of reporting success.
- **Local Ollama summaries** use the same prompt, checks and retries as the
  hosted models. Its old text parser could store a garbled summary.
- **The command-line summarizer works again.** It found no papers (it waited
  for a "downloaded" flag nothing set), used a different database from the web
  app, and repeated each summary's text so reviews sent it twice. It now shares
  the web app's summarize code and database, tries papers that failed last
  instead of first, and `--mock` refuses to write into the real database.

### Notes
- Summarizing needs the paper's DOI or id. A paper known only by a title
  fingerprint can be summarized from a search you just ran or a saved list.

## 2026-09-28 — searches reach every source, and filters mean what they say

Review fixes, batch 1. Gate: `docs/cycles/2026-09-28_review-batch1-qa-gate.md` (APPROVED).
Plan: `docs/implementation_plan_2026-09-28_review_fixes.md`. Live counts before
the fix: `docs/cycles/2026-09-28_retrieval_baseline.json`.

### Fixed
- **Every selected source is searched.** Max results used to be one budget for
  the whole run, so a broad filter used it all on Europe PMC and the other
  sources were never asked. It now applies to each source, and a source cut off
  by it says so ("Europe PMC — skipped (truncated)").
- **PsyArXiv, SocArXiv and arXiv work with saved filters again.** A filter with
  no start/end date sent them empty dates; they answered with errors (HTTP 400
  and 500) and returned nothing.
- **PsyArXiv and SocArXiv see every term.** Only the first word of the filter
  was sent. Each title term is now asked for separately, or, when that could
  miss a match, the date range is fetched and the filter applied here.
- **bioRxiv/medRxiv searches medRxiv too**, over the filter's own dates, and
  knows each paper's version, so "revised only" works.
- **Paper type works.** Europe PMC records were all typed "other" (the adapter
  read a field the API does not send), so "Review" and the other types matched
  nothing — "Loneliness" found 0 of 709. Preprints in Europe PMC are now flagged
  as preprints.
- **A wildcard term matches inside a title** ("adolescen*" finds "Stress in
  adolescents"), not only at its start.
- **Licence filtering compares meaning, not spelling** ("cc_by", "CC BY 4.0" and
  a Creative Commons URL are the same licence; "cc_by" no longer matches
  "cc_by_nc"), and it runs after enrichment, so licences found by Unpaywall
  count.
- Two searches running at once no longer mix up each other's pages.
- Records a source sent but could not be read, and sources that could not be
  reached during abstract recovery, are reported instead of looking like
  "nothing found".
- The same paper from Europe PMC, bioRxiv and arXiv is merged more often.

### Changed
- **Filter options live in `filter_vocabulary.yaml`.** The page builds its
  dropdowns from it. Filters store option ids; ones saved with the old labels
  are read as before. A filter with an option the search cannot apply is
  refused on save.
- **Institution is gone from the filter editor.** No source reports author
  institutions, so an institution term matched nothing. A stored one is ignored
  (with a log warning).

### Notes
- With several sources, a run can now return up to Max results *per source*.
- `sources_config.yaml` gains `osf.max_title_terms` and a `truncated`
  explanation.

## 2026-09-26 — a term click builds up one filter

Gate: `docs/cycles/2026-09-26_term-chips-append-qa-gate.md` (APPROVED).

### Changed
- **Click a suggested term to add it to the filter you have open**, as a new
  group, with the term appended to the filter's name: clicking "sleep" then
  "apnea" leaves one filter called "sleep, apnea" matching either term. With no
  filter open, the first click starts one named after the term.
- **Right-click now starts a new filter** from the term. The two actions have
  swapped: adding term after term is the common one, so it is the plain click.

### Fixed
- Saving a renamed filter failed with "No such filter". A rename is stored under
  the new name and the old row deleted, so the filter's id changes, and the page
  kept the old one. It broke the second term click, and it broke **Save** on the
  Filters tab after any rename.

### Notes
- An appended name never lands on another filter's name — that would overwrite
  it — so it becomes "sleep, apnea (2)" instead.
- Names stop growing at the server's 200-character limit: the term is still
  added as a group, and the message says the name was left as it was.
- On iPhone and iPad there is no right-click. Clicking still works; press
  **+ New** first to start a separate filter.

## 2026-09-21 — build filters straight from suggested terms

### Added
- **Click a suggested term** under Discover Search Terms to create a new filter
  named after it, searching for that term. It is saved at once and appears in
  Saved Filters and in Select Filter on the Search tab.
- **Right-click a term** to add it to the open filter as a new group. Groups are
  OR'd, so the filter matches papers containing any of its terms. With no
  filter open, a right-click starts one.
- Terms already in the open filter are highlighted.

### Fixed
- Clicking a suggested term did nothing on a fresh Filters tab: it only worked
  when a text group already existed, and when it did work it appended into
  whatever filter happened to be loaded.

### Notes
- A new filter never takes a name you already use. Saving a filter under an
  existing name replaces that filter, so clicking "inflammaging" twice makes
  "inflammaging" and "inflammaging (2)" rather than overwriting the first.

## 2026-09-21 — Review checked papers

Gate: `docs/cycles/2026-09-21_batch-summarize-qa-gate.md` (REJECTED, fixed).
Plan: `docs/implementation_plan_2026-09-20_references_batch.md` (M4).

### Added
- **Review checked** on the Saved References tab: one synthesis across the
  ticked papers — shared themes, disagreements, gaps — in a single model call.
- The review says what it was able to read: "Based on 2 full-text summaries and
  4 abstracts." A paper with neither is named as not covered, and so is any
  paper dropped because the combined text was too long to send.
- Reviews are stored and survive a reload. A new review does not replace the
  last, so a synthesis can be compared with one made before more papers were
  summarized.

### Notes
- A review reads only stored text and fetches nothing, so its cost is known
  before it runs rather than estimated.
- A review draws on the same daily allowance as a summary — it cannot sidestep
  the cap by being a different kind of call.
- A list with no summaries and no abstracts is refused without calling the
  model: a review of nothing would be invention.

### Fixed
- The cost dialog could name the wrong payer. A key held only in the browser
  was resolved as the shared key, so the dialog showed an allowance that did
  not apply and could refuse a run the user's own key would have paid for.
  The estimate now resolves credentials through the same function the spend
  path uses.
- A stuck server turned a batch into a silent forever-loop; it now gives up and
  says so, as the single Summarize button already did.
- A second click could start a concurrent batch and bill twice on a user's own
  key.

## 2026-09-20 — Summarize checked, with a cost estimate first

Plan: `docs/implementation_plan_2026-09-20_references_batch.md` (M3, M5).

### Added
- **Summarize checked** on the Saved References tab. Papers are summarized one
  at a time through the same per-paper path the single Summarize button uses,
  so the shared-key daily allowance is claimed per paper exactly as before.
- **A confirm dialog before anything is spent**, showing how many papers, an
  estimated token range, whose key pays, and how much of today's shared
  allowance is left. Nothing is sent until you agree.
- `GET /api/usage/estimate`.

### Notes
- The estimate is a range and says so. Its upper bound is not a guess:
  `max_text_chars` caps what is ever sent to the model, so no summary can
  exceed it. What the estimate cannot know is which end a given paper falls at.
- A dollar figure appears only for models with a rate in `llm_config.yaml`.
  Anthropic's rates ship; DeepSeek's are left for you to fill in from their
  pricing page, because a wrong price is worse than no price. Token counts show
  either way.
- Papers that already have a summary are skipped and counted. One failure does
  not abandon the rest. A run that the allowance stops part-way says so, and
  the report is always "N of M", never a bare "done".
- A paper with no free full text has its abstract kept and no model is called.
  Those are reported separately — "Summarized 1 of 3; 2 kept as abstract only"
  — because counting them as summarized would claim work the model never did.
- DeepSeek's rates are now in `llm_config.yaml`, from their published pricing
  page (checked 2026-09-21). They are the peak rates; DeepSeek charges half
  off-peak, so the estimate is an upper bound on the price.

## 2026-09-20 — Saved References: Select All, and Save as CSV/RTF/PDF

Plan: `docs/implementation_plan_2026-09-20_references_batch.md` (M2, M6).

### Added
- **Select All on the Saved References tab.** One box in the table header ticks
  or clears every paper in the list, and shows the mixed state when only some
  are ticked, so it never claims a whole-list selection that is not there.
- **Save Reference List**, replacing Export CSV, with a format choice: CSV for
  a spreadsheet, RTF for Word or Pages, or PDF. Ticked papers only when any are
  ticked, the whole list otherwise. The file is named after the list.

### Changed
- The saved file is named after the list rather than `references.csv`.
- `GET /api/references/{id}/export.csv` still works — it is a plain URL someone
  may have bookmarked — and now renders through the same code as the new
  endpoint, so the two cannot drift.

### Notes
- RTF is written directly, with no new dependency. Greek letters, accents,
  curly quotes, braces and backslashes in titles are escaped and verified to
  round-trip through the system RTF reader that TextEdit and Pages use.
- Spreadsheet formula injection is defused in all three formats, not only CSV:
  text copied out of a PDF into a spreadsheet is just as live.

## 2026-09-20 — token accounting, and a session meter

Gate: `docs/cycles/2026-09-20_token-capture-qa-gate.md` (REJECTED twice, then
APPROVED). Plan: `docs/implementation_plan_2026-09-20_references_batch.md` (M1).

### Added
- **A token meter in the header** showing what this session has spent, e.g.
  `18.4k tokens this session`. It refreshes whenever something calls a model,
  including when that call fails. Calls whose provider reported no counts are
  named separately (`18.4k tokens + 2 uncounted`) rather than folded in as zero:
  a total that quietly omits them would read as complete when it is not.
- `GET /api/usage/session`. The window starts at the session cookie's issue
  time, so signing out and back in starts a fresh count, and no new server-side
  state is needed.
- BioRx now records what every model call costs. No provider kept the usage
  block the APIs already return, so nothing downstream could report spend.
  `usage_events` gains `prompt_tokens`, `completion_tokens` and
  `tokens_counted` (additive migration; rows written before it read as
  *unknown*, never as free).

### Fixed
- A summary or discover run that failed **after** the model was called recorded
  no tokens. The call had been billed and was invisible in the log.
- Discover runs on a user's own key were never written to the usage log at all.
- Downloaded PDFs were named `paper-<rowid>.pdf`. They are now named after the
  paper, matching the desktop app's convention.

### Known limitation
- Token counts are what the provider reports. Ollama omits them on some
  versions; those runs show as uncounted rather than free.

## 2026-09-19 — Search panel: sticky tabs, Select Filter, Ad Hoc Search

Gate: `docs/cycles/2026-09-19_ui-enhancements-qa-gate.md` (APPROVED).

### Changed
- **The tab bar stays at the top of the viewport** while a panel is scrolled, so
  the tabs never scroll out of reach. `.tab-bar`/`.tab` had no styling at all
  before this, so the tabs also gain a visible active state. The notice banner,
  already sticky, was moved from `top: 8px` to `top: 52px` so it parks below the
  bar instead of behind it.
- **Saved Filters in the Search panel is now a "Select Filter" dropdown** with a
  single Run button, replacing a list that grew one row per saved filter. The run
  state the per-filter buttons carried is kept: the Run button reads Run /
  Running… / Done / Stopped / Failed / Lost track for the filter now picked, the
  button and the dropdown are both disabled before the request leaves, and the
  selection survives a rebuild of the list. With nothing saved, the dropdown says
  so and Run is disabled. The Filters tab still manages saved filters.
- **The manual search card is "Ad Hoc Search"**, told apart from running a saved
  filter. Tab names and the Search/Stop buttons are unchanged.

### Known limitation
- The sticky layout and the 52px clearance are browser-render properties the
  headless suite cannot verify; the test pins the CSS value against drift but
  cannot prove the bar is actually 52px tall. See TODO.

## 2026-09-19 — summaries from full text, and finding free copies

Gates: `docs/cycles/2026-09-19_full-text-qa-gate.md` (REJECTED, F1–F5 fixed),
`docs/cycles/2026-09-19_full-text-regate-qa-gate.md` (APPROVED).
Plan: `docs/implementation_plan_2026-09-19_full_text.md`.

### Changed
- **No summary without full text.** Before summarizing, BioRx looks for a free
  copy: the paper's own link, Unpaywall, OpenAlex, Semantic Scholar (DOI, else
  exact title + first author or year). With none found, the model is not called
  and nothing is charged: the abstract is kept, labelled "Abstract only — no full
  text found (not a model summary)" with a ✓ Abstract only badge, in the details
  view, summaries panel and summaries PDF. Summarize on it later searches again.
- A downloaded PDF must contain the paper's title, or it is rejected as another
  document. Full-text summaries record where the text came from.
- The desktop/CLI summarizer follows the same rule.

### Added
- Settings: "Also look for free copies by title" (default from
  `sources_config.yaml` `full_text.find_by_title`). `/healthz` lists the active
  full-text finders.

### Not added
- Google Scholar and ResearchGate: no public API; both forbid automated access.
- CORE: needs an API key.

## 2026-09-18 (filter runs, model default, saved lists)

Gate: `docs/cycles/2026-09-18_filter-run-batch-qa-gate.md` — APPROVED.
Plan: `docs/implementation_plan_2026-09-18_filter_run.md`.

### Added
- Saved reference lists: each paper has a **detail** view, a **✓ Summary**
  badge and a **Summarize** button, as on the Search results. **Save summaries
  (PDF)** exports the ticked papers, or the whole list when none are ticked.
  New `GET /api/references/{id}/summaries`.
- Live progress line: `Found · Matched · Enriched` while a search runs, on
  the Search tab and the Filters-tab test.
- Saved-filter Run buttons read Running… → Done / Stopped / Failed / Lost track;
  all Run buttons are disabled while a search is in flight.
- `DEFAULT_LLM_PROVIDER` names the default provider (old name `LLM_PROVIDER`,
  still honoured; the start-up log flags it and says which one won).
- The web app, desktop app and CLI agents read `.env` at start-up (never
  overriding the environment). The web app logs which settings came from where
  (names only) and now logs at INFO — before, every INFO line was discarded.

### Changed
- Default LLM is **DeepSeek** (`deepseek-flash`). Ollama's entry names the
  installed `qwen3.5:4b`; `qwen:7b` was never installed.
- Only papers the filter keeps are enriched (Crossref/Unpaywall), and what
  enrichment finds (PDF links, licence, abstracts) now reaches the results —
  web, desktop and saved rows. It used to run on every fetched paper and its
  output was thrown away.
- A saved filter runs on its own saved sources, not the Search panel's boxes.

### Fixed
- An empty filter (no terms, authors or institution) is refused everywhere
  instead of scanning every source's whole date window.
- "N were fetched" showed the enrichment count.
- Crossref/Unpaywall outages are reported (warning line, status, `monitor.py`
  exit 2) instead of logged at debug.
- One failed status check no longer ends a search as Failed while it runs on.
- The desktop summarizer used a hard-coded, uninstalled model and labelled every
  summary "qwen:7b"; it now uses the config, records the real model, refuses
  blank summaries, and the CLI names a paid model before running.
- Ollama: checks its model is installed; honours the configured text budget and
  timeout; its prompt no longer sends a Python comment to the model.
- An unreachable abstract source is reported as unreachable, not "looked in".
- Desktop: a later filter can no longer erase a PDF link an earlier one found;
  a failed filter no longer leaves the run buttons disabled for good.
- A top-level `keywords` string was split into letters.
- `llm_config.yaml` no longer advises pasting API keys into a committed file.

## 2026-09-18 (review) — fixes from the pre-push review

Three reviews (learning-qa, correctness, security) over everything since the
last push. All findings fixed, each with a test:

### Security
- **PIN lockout could be bypassed by sending guesses in parallel** (about 190
  checks per window instead of 5). Each attempt now takes its slot in one
  atomic UPDATE before the PIN is checked.
- Resetting (`reset-pin`) or recovering a PIN now ends every session opened
  before it (a session epoch in the signed cookie).
- Recovery is refused, before anything changes, for a person whose access code
  is turned off or expired.
- The public code lookup no longer runs a dummy password hash (free CPU for
  anyone); a code tied to a missing account is refused there too.
- Security headers: `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy` on every response; a Content-Security-Policy on the page.
- A non-ASCII shared code gave a 500; now a 401.
- `access_codes.example.yaml` no longer ships live example codes.

### Fixed
- A broken or vanished codes file told each person their own code was turned
  off; it now says sign-in is unavailable and to tell the owner (503).
- `expires: 20270101` (a number) and a blank `disabled:` were accepted quietly;
  now refused / treated as disabled, with a warning.
- Merging into an account that is itself merged is refused (data would be
  unreachable); a looped merge chain signs the cookie out instead of using the
  old account.
- `add --account` checks the account exists; `renew` keeps a comment with the
  entry it belongs to.
- Summarize buttons stay disabled while that paper's summary runs (a redraw
  re-enabled them, so a second click billed twice).
- Signing out, or being signed out, reloads the page so the next person on the
  device sees none of the last person's results or summaries.
- PDF and CSV downloads go back to the sign-in page when signed out, and report
  network errors instead of hanging on "Building…".
- Search summaries list a paper once when two results are the same paper; PDF
  filenames with non-Latin titles no longer fail.
- Second review round (of the fixes themselves): PIN resets, recoveries and
  merges now end cookies of merged-away accounts too; a sign-in that races a
  PIN reset is refused; one mistyped entry tells that person "your entry has a
  mistake, tell the owner" (503) instead of "turned off"; a blank, broken or
  vanished codes file refuses old-style accounts as well (it could let a
  disabled one in); recovery refused for a server problem is a 503 and does not
  cost a lockout attempt; the search PDF counts a paper once; a lock is logged
  once; a network blip while a summary runs keeps its button busy; sign-out
  errors are shown.
- Third round: session cookies carry a random per-account value instead of a
  counter (counters of two accounts could meet after a merge and revive a
  cookie a reset had ended); `reset-pin` also clears PINs of accounts merged
  into that one; a flow-style, BOM-prefixed or quoted-key codes file is no
  longer mistaken for a blank one; `/healthz` names a blank or unreadable file
  while codes are in use; a file that cannot be opened at start fails closed;
  a mistyped entry that names an old-style account refuses that account; the
  summary poll warns after repeated network failures and never overlaps.
- Fourth round: a cookie is checked against the account it names, and a reset
  renews the value of every account merged into that one (at any depth), so an
  ended cookie stays ended after a later merge and a merge never signs anyone
  out; `reset-pin` clears PINs at every merge level; a codes file recreated
  unreadable after going missing counts as down, and its error is logged once;
  `account:` entries naming a merged-away account still apply.
- Fifth round: an unreadable codes file is served from the last good copy for
  at most `ACCESS_CODES_READ_GRACE_SECONDS` (60), then sign-in stops (before, a
  just-disabled person stayed in for as long as the file was unreadable);
  file states are logged once per change; merges of any depth work (a limit of
  5 signed people out); codes bound deep in a merge chain still apply; the
  cookie names the row whose PIN was checked.
- Sixth round (no high findings left): first use of a code and PIN recovery
  also carry the session value set in their own transaction; a codes folder
  that cannot be searched is handled like an unreadable file (grace, then
  closed) instead of raising on every request; a bad grace setting is warned
  once per incident.
- Last: recovery on a broken (hand-edited) merge chain is refused before the
  PIN or recovery code changes.
- Account ids are random and 1 in 64 starts with `-`, which the command line
  read as an option (`--user-id -abc` failed; this was the flaky test). Docs,
  help text and tests now use `--user-id=ID`, `--from=ID`, `--into=ID`.


## 2026-09-18 (late) — personal access codes

### Added
- **One access code per person**, kept readable in `DATA_DIR/access_codes.yaml`
  (git-ignored; format in `access_codes.example.yaml`). Sign-in is code + PIN;
  the browser can remember the code, so usually only the PIN is asked for
  ("Welcome back, NAME"). A new code creates its account on first use, with a
  PIN chosen then. One account per code.
- `python -m src.access_codes add | renew | reset-pin | list`. Codes last
  `ACCESS_CODE_DAYS` (180). `disabled: true` or deleting an entry turns a person
  off; an expired or disabled code also ends their open session on the next
  request. The file is re-read when it changes; bad entries are skipped and
  logged, and `/healthz` shows how many.
- `POST /api/session/lookup` (public): the name for a code and whether a PIN is
  set, for the "Welcome back" step. Nothing else.

### Changed
- The shared `ACCESS_CODE` no longer creates accounts. While set, accounts made
  before codes can still sign in with it + name + PIN; `account:` in the codes
  file ties a code to such an account, keeping its PIN and data.
- Forgotten PINs are reset by the owner (`reset-pin`); new accounts get no
  recovery code.

### Removed
- "Create account" by name, and Settings → "Your account" name + PIN claim
  (`POST /api/me/account`). A code now does both.


## 2026-09-18 (night) — DeepSeek Flash, thinking off, key help

### Changed
- DeepSeek default model is `deepseek-flash` (`deepseek-chat` is no longer in
  DeepSeek's model list; checked against api-docs.deepseek.com 2026-09-18).
- DeepSeek requests send `thinking: {"type": "disabled"}` (config:
  `providers.deepseek.thinking` in `llm_config.yaml`). deepseek-flash thinks by
  default at high effort, which adds output and time to every summary.

### Added
- Settings → LLM settings: "How do I get a DeepSeek key?" opens a short guide
  (sign up, top up, create a key, paste it here) with the cost per summary.


## 2026-09-18 (evening) — web accounts: name + PIN

### Added
- **Accounts.** After the shared access code, each person signs in with a name
  and PIN; the same name and PIN always return the same account, on any browser.
  Previously every sign-in created a new, cookie-only user, so filters and Saved
  References were lost with the cookie.
- **Recovery code**, shown once at account creation; "Forgot PIN?" sets a new PIN
  with it and issues a new code.
- Lockout after repeated wrong PINs or recovery codes (`LOGIN_MAX_FAILURES`,
  `LOGIN_LOCK_MINUTES`); PINs and codes stored as scrypt hashes.
- Settings → Your account: give an existing cookie-only account a name and PIN.
- `python -m src.accounts merge` combines two users (used to merge the two
  "dave" users in the test database; identical seeded filters are not copied).


## 2026-09-18 (later) — no stale pages; summaries accumulate and save from Search

### Added
- **Summaries panel** on Search & Browse: every stored summary for the current
  results, newest first. A new summary is added, never replacing another.
  Result rows show "✓ Summary".
- **Save summaries (PDF)** on Search & Browse: ticked papers, or all results.
- Startup warning when an owner API key looks pasted twice or contains spaces or
  quotes (the key itself is never shown).

### Fixed
- Browsers kept showing the old page after an update. The page and its files
  are now served `Cache-Control: no-cache`, and the script/stylesheet links
  carry a content hash.
- The "All" checkbox is cleared when a new search starts.
- "Save to Saved References" opens a name dialog (it used to toggle a small box
  that a second click hid); a taken name offers "… (2)".
- A failed summary shows its reason inside the popup.
- Source pickers start from the server's defaults (bioRxiv and arXiv off)
  until you choose your own; "could not reach" now says why.


## 2026-09-18 — web UI fixes, desktop naming, summaries PDF

### Added
- **Export summaries (PDF)** on the Saved References tab: one PDF per list — a
  cover page ("N of M papers summarized"), one page per summarized paper (key
  findings, methodology, conclusions, and the model that wrote it), then the
  papers without a summary listed under "Not summarized". Only stored summaries
  are printed. Needs a Unicode font: `BIORX_PDF_FONT`, else DejaVu (installed in
  the Docker image) or Arial Unicode on macOS; without one, unshowable
  characters are replaced and counted on the cover.

### Fixed
- The paper detail popup and summaries rendered ~2,900 px down the page and
  looked like they did nothing. The popup is now an overlay (✕, Esc or click
  outside to close), and Summarize shows its progress and result inside it.
- The message bar stays visible while scrolling.
- "Save to Saved References" is enabled whenever there are results: "Save all N
  results", or "Save selected (N)" when papers are ticked. The list name is
  pre-filled with the filter name (or search words) and the date.

### Changed
- Web labels match the desktop app: tabs "Search & Browse", "Filters", "Saved
  References", "Settings"; the Search tab's list is "Saved Filters".

## 2026-09-17 — web app review fixes (before first push of the parity work)

Found by learning-qa, /code-review and /security-review over the unpushed range.

### Security
- **PDF proxy SSRF closed** (`src/safe_fetch.py`). The proxy checked only the
  first URL and then followed redirects, missed link-local (cloud metadata) and
  CGNAT ranges, and treated an unresolvable name as safe. Now every hop must be
  https and resolve only to public addresses, the connection is pinned to the
  checked address with TLS verified against the hostname, the body is size-
  capped while streaming, and it must actually be a PDF.
- **Removed `POST /api/references/{id}/items`**, which stored a client-supplied
  paper (and its URL) for the proxy to fetch. Papers enter lists through
  save-as-list only.
- **Summaries fetch through the same guard.** `POST /api/summaries` downloaded
  the client's `pdf_url` and scraped its abstract URLs with plain `requests`,
  so an internal page could be read back through a summary, and the PDF was
  cached under the client's DOI/title where later summaries of the real paper
  would read it. Both now go through `src/safe_fetch.py`, into a temp file.
- DNS failures are a retryable 502, not a 403; a whole download has a time
  limit that holds even against a server trickling bytes; the PDF signature
  may follow leading bytes.
- A summary made from the abstract alone now says so, and why ("from the
  abstract only (full text not used: the link leads to a web page…)"). An
  `http://` PDF link is tried as `https://`.

### Fixed
- **Discover Terms** searched on single letters of the description, never showed
  its results, kept a daily-cap slot when it failed, and reported unparsable
  replies as "no terms". Settings and stop words are now in `llm_config.yaml`.
- **Filters saved from the web** lost their keywords (so matched everything),
  crashed every search (institution saved as a list), and ignored date ranges.
  Filters already stored in that shape are converted on the server wherever
  they are read or run (`src/filtering.normalise_filter`).
- **Reference lists:** deleting a list now deletes its items; a duplicate name
  is a 409, not a 500; save-as-list waits for the search to finish and reports
  papers it could not store; CSV exports the right bioRxiv version.
- Bulk PDF download and remove report what failed.
- `/api/me` reports the model that will actually run.
- A Discover run that finds no papers gives its daily-cap slot back.
- Saving or removing an API key reports a server failure instead of claiming
  success (a key could stay stored and billed after "Key removed.").
- Filter Test says when a source could not be reached, as the Search tab does.
- The PDF proxy tries an `http://` link as `https://`, like the summary path.

## 2026-09-17 — web Settings tab is per user

### Changed
- **The web app no longer edits the server's config files.** The Settings tab
  added in the parity commit let any signed-in user read and overwrite
  `sources_config.yaml` and `llm_config.yaml`, changing the app for everyone.
  `web/routes_settings.py` is removed; those files are owner-only.
- **Settings tab now holds each user's own settings:** the LLM panel (moved from
  the header) and new **default sources**, saved in the browser, which set the
  sources ticked in the Search tab and in new filters.
- The contact email moves out of the tracked `sources_config.yaml`; set
  `BIORX_CONTACT_EMAIL` in the environment.

### Fixed
- Opening a saved filter in the web app showed blank fields, and saving it then
  erased its keywords, dates and sources. The client read `f.filter`, a key the
  API never returns.
- Source checkboxes stacked above their labels, one per row.

## 2026-09-16 — polite User-Agent for all API calls (batch-H)

### Added / Fixed
- **Every HTTP request to a polite-pool API now sends a correct `biorx/1.0`
  User-Agent** (and `biorx/1.0 (mailto:EMAIL)` when a contact email is set).
  This applies to all eight consumers: EuropePMC, PubMed, PsyArXiv, SocArXiv,
  bioRxiv/medRxiv, Crossref, arXiv, and PDF downloads (both `pdf_handler.py`
  and `monitor.py`). Previously six adapters sent `ResearchTool/1.0` or no UA,
  and PDF downloads sent no UA at all.
- **Contact email** is read from `BIORX_CONTACT_EMAIL` env or `contact_email`
  in `sources_config.yaml`; no personal address is embedded in source code.
- **Startup warning** when no contact email is configured. The warning is:
  - Logged at startup for both GUI and CLI.
  - Shown in the GUI status bar (all warnings joined, never overwriting).
  - Returned by `/healthz` in the web app as `startup_warnings[]`.
  - Displayed by the web UI on boot via `startup_warnings.join(" | ")`.
- `PDFHandler()` with no `output_dir` argument now resolves via `DATA_DIR`
  environment variable instead of crashing. Default remains `~/preprints/PDFs`.

### Tests
- `test_h_environment.py`: 16 new tests covering all eight consumers, the
  startup-warning flow, pdf_handler default constructor, and monitor UA.
- `test_app.py`: `test_h_app_js_reads_healthz_startup_warnings_on_boot`
  asserts the render call (`startup_warnings.join`) survives comment-stripping
  (P27 mutation guard).
- `test_frontend_wiring.py`: `/healthz` added to the exact endpoint set.
- Retrieval-layer BASELINE advanced four times to `a55b83e`; test gates each
  advance against the drift guard.

## 2026-09-16 — recovering a missing abstract (N2)

### Fixed
- **A summary no longer fails just because the search record lacked an
  abstract.** Before giving up, the web app now looks for one — Europe PMC by
  DOI, PMC full text, Crossref, OpenAlex, then the paper's own pages — and
  stores what it finds with the paper. The paper that exposed this, an open-
  access article on PMC with no DOI, now recovers its full abstract.
- The desktop app's abstract lookup had the same gaps: it skipped PMC for any
  paper without a DOI, and reported "not available" as though it were the
  abstract. Both apps now share one lookup.
- A correction, erratum or retraction notice with no text is refused with a
  message saying so. The list of notice titles is in `llm_config.yaml`.
- Every source is held to a minimum abstract length, so a short error string
  or page tagline is never accepted as an abstract.

### Tests
- The whole suite now fails any test that tries to reach the internet, even if
  the code under test catches the error. It found one test that had been
  making live calls and failing intermittently.

## 2026-09-16 — papers without a DOI (N1)

### Fixed
- **Papers without a DOI can now be stored.** `papers.doi` was `NOT NULL`, so
  every arXiv record and many PubMed and PsyArXiv records silently failed to
  save — in the web app their summaries were paid for and lost, and in the
  desktop app "save to database" skipped them. Identity is now `canonical_id`,
  with a unique index; a paper with neither identifier is refused with a
  warning.
- Looking up an existing summary finds DOI-less papers, so re-opening an arXiv
  summary no longer re-runs (and re-bills) the model.

### Migration
Existing databases are rebuilt once, on first open, to drop `NOT NULL` from
`doi`. It runs in a single transaction with a row-count check, preserves every
column and the id counter, and refuses to run on a table definition it does not
recognise. Rehearsed on a copy of the live database (2,623 papers): identical
content, 0.04s.

## 2026-09-16 — the web client and deployment

### Added
- **A single-page client** (`web/static/`): vanilla HTML, CSS and JS with no
  build step, so the whole app is one deployable container that works offline.
  Saved searches with Run, a manual search form, a paginated results table with
  per-paper PDF and Summarize, and an LLM settings panel. It mirrors the desktop
  app's screens and adds nothing beyond them.
- **`Dockerfile`, `docker-entrypoint.sh`, `.dockerignore`, `railway.json`** and
  a documented `.env.example`. The image installs `requirements-web.txt` only,
  runs the app as an unprivileged user, and writes solely to the mounted volume.
- **README** sections on running locally, how access and credential precedence
  work, why searches are jobs, deploying to Railway, and a manual checklist for
  what CI cannot test.

### Fixed
- A search that matched nothing now says how many papers were fetched and
  filtered out. "0 matching" alone cannot be told apart from "the sources
  returned nothing".
- Link URLs from external APIs pass through a scheme check, so a
  `javascript:` URL in a paper record cannot run on click.
- The placeholder `ACCESS_CODE` from `.env.example` is treated as unset rather
  than as a live credential that is readable on GitHub.
- The container makes its mounted volume writable at **runtime**. A build-time
  `chown` does not survive a volume mount, so without this the first database
  write fails on startup. The app also refuses to start, naming the directory
  and uid, rather than surfacing a bare sqlite error later.

### Known
The container image has never been built: no Docker daemon was running on the
development machine. The first `docker build` is the deployer's, and the README
says so.

## 2026-09-15 — the web app backend

### Added
- **A FastAPI backend** (`web/`) so colleagues can use the retrieval pipeline
  from a browser. One shared `ACCESS_CODE` is exchanged for a signed cookie
  carrying a server-issued opaque user id; the display name is a label with no
  authority, so nobody can assume a colleague's identity — and therefore their
  API key — by typing their name.
- **Searches run as background jobs.** A multi-source search takes minutes and
  cannot be held open by an HTTP request. `POST /api/searches` returns a job id;
  the client polls status and pages results. Cancellation, expiry (410, "run it
  again") and per-user ownership are all enforced.
- **Summaries** with the pluggable LLM backend, under a per-user daily cap on
  summaries billed to the owner's key.
- **Bring-your-own-key**: a colleague can store their own DeepSeek or Anthropic
  key, encrypted at rest. Only its last four characters ever leave the server.
- **Per-user saved filters** in SQLite, seeded from `filters.json` on first
  sign-in, because a single shared file in a container is last-write-wins.
- `src/user_store.py` for users, their filters and their usage.

### Fixed
- The owner-key spend cap was a check-then-act race: the count was read at
  submission and written when the job finished, so a burst of requests all
  passed. A slot is now reserved atomically at admission.
- The user-supplied abstract was not budgeted before being sent to the model,
  so one crafted request could send an arbitrarily large prompt on the owner's
  key. Both prompt halves are budgeted and the request body has a ceiling.
- A segmentation fault on shutdown: a job worker mid-write while the database
  closed under it. Shutdown now drains with a bounded wait and the caller only
  closes shared resources when it succeeded. This would have hit on redeploys.

## 2026-09-15 — pluggable LLM backends and background jobs

### Added
- `llm_config.yaml` + `src/llm_config.py` — provider dialects, model ids, the
  paper-text budget and the owner-key spend cap, in configuration rather than
  source. `LLM_PROVIDER`, `ANTHROPIC_MODEL`, `DEEPSEEK_MODEL` and
  `SUMMARY_DAILY_CAP_PER_USER` override the file.
- `src/llm_providers.py` — `DeepSeekClient` (OpenAI-compatible dialect),
  `AnthropicClient` (official SDK), and `resolve_client()` with the precedence
  user key → owner key → a typed error naming both what the user should do and
  what the operator should set. Hosted replies are requested as JSON against a
  schema and validated, so a reply in the wrong shape raises instead of storing
  a blank summary.
- `src/crypto.py` — Fernet encryption of a user's API key, plus masking.
  `KEY_ENC_SECRET` is required from the environment and never generated.
- `src/jobs.py` — an in-process job registry for searches and summaries, which
  take minutes and cannot be held open by an HTTP request. Owner-scoped,
  cancellable, expiring, and guarded so a worker can never leave a job on
  "running".
- `requirements-web.txt`, separate from `requirements.txt` so PyQt6 is never
  installed in the container.

### Note
The Anthropic default model is `claude-sonnet-5`. `claude-sonnet-4`, which the
original brief specified, is not a real model id.

## 2026-09-15 — web app groundwork

### Added
- `src/paper_meta.py` — PDF/landing-page URL resolution and abstract recovery
  (JSON-LD, meta tags, HTML patterns, OpenAlex inverted index), extracted from
  `gui.py` so a second front end can reuse it.
- `src/filters_store.py` — filters.json persistence and the pure predicates
  `filter_is_enabled()` / `filter_has_text()`.
- Database tables for the planned web app: `users`, `user_filters`,
  `usage_events`; `summaries.created_by_user_id` for provenance.
- `Database.release()` and automatic per-thread connection release.
- `docs/implementation_plan_2026-09-15.md` — the approved web-app plan.

### Changed
- **`Database` gives each thread its own connection.** The single shared
  connection was justified by "writes are always serial (one worker at a time)",
  which is true of a desktop GUI and false of a web app. WAL journal mode, a
  busy timeout from `BIORX_DB_BUSY_TIMEOUT_MS`, and `close()` now closing every
  outstanding handle.
- Database and contact configuration is environment-driven: `BIORX_DB_PATH`,
  `DATA_DIR`, `BIORX_CONTACT_EMAIL`. Nothing resolves to a developer's home
  directory in a container or a test.
- `insert_summary()` records `created_by_user_id` alongside `model_version`.

### Fixed
- Per-thread connections are released when their thread exits — including on
  **QThread**, where `threading.current_thread()` is a `_DummyThread` whose
  `is_alive()` never goes False. Release is keyed on the lifetime of the
  thread-local holder via `weakref.finalize`, so it does not depend on
  thread-object semantics. `SearchWorker` also releases explicitly.
- Schema migrations read `PRAGMA table_info` instead of wrapping every
  `ALTER TABLE` in `except Exception: pass`, which made a real migration failure
  indistinguishable from "column already exists".

## 2026-09-15

### Added
- **arXiv source** (`src/sources/arxiv.py`) — covers the CS / LLM-agent /
  agent-based-simulation literature none of the other eight sources index. Atom
  XML parsing, descriptive User-Agent, >= 3s request spacing, `Retry-After`
  honoured on 429. Registered in the orchestrator and the source picker.
- **arXiv query builder** — `build_arxiv_query()` mirrors the Europe PMC group
  semantics with arXiv field syntax (`ti:` / `abs:` / `all:` / `au:`) plus a
  `submittedDate` range.
- **Headless CLI** (`agents/monitor.py`) — runs saved filters without PyQt6 and
  emits JSONL, so cron can drive the multi-source layer.
- **`Agent Simulation` filter** in `filters.json` — six facets covering agent
  architecture, algorithmic fidelity, persona/emotion, self-adapting models,
  emergent misalignment, and computational behaviour.
- `CHANGELOG.md` and `TODO.md`.

### Changed
- **`src/filtering.py` is new and now owns a saved filter's client-side
  semantics**, extracted from `gui.py`. The GUI imports it under its former
  private names; the CLI applies it too, so the same filter no longer returns
  different sets on different front ends.
- The orchestrator's last-page detection uses the source's own page size when an
  adapter reports one, instead of the number of records it returned.

### Fixed
- `authors` in a filter is read through `normalize_authors()`, which accepts both
  the list the GUI writes and the comma-separated string older filters use.
  Previously the arXiv query builder crashed on a list, and the client-side
  filter iterated a string character-by-character and matched nearly everything.
- A withdrawn arXiv paper on a full page no longer truncates the search: dropping
  it shortened the returned page, which the orchestrator read as "last page".
- Withdrawal detection is an anchored match on arXiv's conventional notice rather
  than a bare `"withdrawn"` substring, which deleted real papers whose abstracts
  merely mention withdrawal. Drops are counted and logged.
- arXiv version suffixes are stripped by regex, so old-style identifiers such as
  `cs.CV/0701001v1` produce a correct `canonical_id`.
