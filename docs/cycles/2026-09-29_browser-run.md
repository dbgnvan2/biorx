# Browser run — common flows, 2026-09-29

Plan section 11 of `docs/implementation_plan_2026-09-28_review_fixes.md`:
"Front-end flows end to end — common-flow browser run, with screenshots".
Screenshots: `docs/cycles/2026-09-29_browser-run/` (numbered in the order taken).

**Setup.** Local server (`.claude/launch.json` → `biorx-browser-run`, port 8767)
on a fresh, empty data folder, with a personal access code made for the run
(`python -m src.access_codes add --for "Browser Test"`), the local Ollama model
`qwen3.5:4b`, and live publication sources. Production was not used: signing in
there needs the owner's own code and PIN.

## Flows

| # | Flow | Result | Screenshot |
|---|---|---|---|
| 1 | New code → choose PIN → signed in | works; header shows the model | 01–03 |
| 2 | Run saved filter "Inflammation" | runs; Run/Search disabled while running; 119 matching; all six sources answered | 04–05 |
| 3 | Page through results | "1–25 of 119" → "26–50 of 119"; Previous disabled on page 1 | 06–07 |
| 4 | Summarize a paper with no free full text | abstract kept, labelled "not a model summary", with where it looked | 08 |
| 5 | Summarize a paper with full text | **failed** ("provider returned an empty response") → fixed, see F1 | 09–10, 13 |
| 6 | Ad-hoc search, bioRxiv/medRxiv only | **1 match from 60 of ~4,900 papers** → fixed, see F2 | 11–12 |
| 7 | Save results as a reference list | saved, list shows 5 papers, summary mark kept | 14–15 |
| 8 | Summarize checked → Cancel | dialog shows papers, estimate and payer; Cancel sends no summary request (network log); batch buttons disabled while the dialog is open | 16–17 |
| 9 | Review checked → Go ahead | review stored and shown; says it was based on abstracts only | 18–20 |
| 10 | Filters tab: open a filter; Save as a case-only duplicate name | facets load from the vocabulary (species "Human studies only"); duplicate refused with a clear message | 21–23 |
| 11 | Settings: save a key for Ollama | server refused it, but the browser **kept it** → fixed, see F3 | 24–27 |
| 12 | Europe PMC abstract text | raw `<h4>` tags shown → fixed, see F4 | 08, 28 |
| 13 | Sign out → wrong PIN → right PIN | "Welcome back"; wrong PIN refused; right PIN signs in, data kept | 29–30 |
| 14 | Phone width (375 px) | no sideways scroll (page width 375) | 31 |

## Found and fixed

- **F1 — local summaries always failed.** `qwen3.5` is a thinking model: asked
  for JSON it put the whole answer in Ollama's `thinking` field and returned an
  empty `response`. Reproduced with a direct call. Fix: `llm_config.yaml`
  `providers.ollama.thinking: disabled` sends `think: false` (the existing
  per-provider `thinking` setting, now honoured by the Ollama client); an
  answer only in `thinking` is logged with the setting to change.
  Tests: `tests/test_tokens.py::test_ollama_think_flag_follows_config`,
  `::test_ollama_config_disables_thinking_for_qwen35`,
  `::test_ollama_answer_only_in_thinking_is_named_in_the_log`.
  After: screenshot 13, a full-text summary from the local model.
- **F2 — bioRxiv/medRxiv read about 1% of their papers, silently.** The
  details API now sends 30 papers per call (it was 100; checked with direct
  calls: 3,433 bioRxiv and 1,491 medRxiv papers for 15–29 Sep). The adapter
  took a page shorter than 100 as the last page, so each search read 30 per
  server and reported nothing. Fix: each server's cursor advances by what it
  sent and it is done when its reported total is reached; the adapter says
  `has_more`, and the orchestrator trusts that over "shorter than our 50".
  The Max results limit now cuts it off visibly ("had more results than Max
  results allows", screenshot 12). The batch-1 after-run
  (`2026-09-28_retrieval_after.md`) read "30 → 60" as an improvement; it was
  this bug. Tests: `tests/test_adapters.py::test_b7_reads_past_a_30_paper_page`,
  `tests/test_orchestrator.py::test_b7_orchestrator_reads_a_30_per_page_source_to_its_budget`.
- **F3 — a refused key stayed in the browser.** Saving a key with Ollama
  selected: the server refused it (batch 7, M8), but the page had already
  stored it, and sent it with every summary, each then refused with a 400.
  Fix: `/api/me` lists `keyless_providers` (from config); the page refuses a
  key for one before storing anything, and switching to one clears a key kept
  in the browser. The "Local key saved (…0000)" hint is now reset when the
  key goes (screenshot 27 was taken before that part of the fix).
  Tests: `tests/web/test_frontend_wiring.py::test_br1_*`,
  `tests/web/test_llm_key_routes.py::test_br1_me_lists_keyless_providers`.
- **F4 — Europe PMC abstracts showed HTML tags.** Europe PMC sends
  `<h4>Objective</h4>`, `<p>`, `<sub>`; the page (correctly) escapes them,
  so they showed as text. Fix: `src/sources/markup.py` turns known tags into
  text ("Objective: …"), shared with Crossref's JATS abstracts. Crossref's old
  helper removed anything between `<` and `>`, which eats "p < 0.05 … x > 1";
  only known tag names are removed now. Summaries stored before the fix keep
  their tags. Tests: `tests/test_markup.py`.

## Found, not fixed — then fixed at the owner's request (same day)

Checked in the browser afterwards; screenshots 32–37.

- **bioRxiv/medRxiv budget (owner's decision: count matches, with a page
  limit).** The adapter declares `filters_locally`; the orchestrator applies
  the filter (without the licence condition, B5) to each page and counts only
  matches against Max results. `publication_sources.biorxiv_medrxiv.max_pages:
  150` bounds the reading; a cut is reported as its own kind, `page-limit`,
  with its own message. The progress count is matches, as for other sources;
  papers read go on the status line (gate F2). Live: "inflammation" over 14 days read all 4,927
  papers in about 3 minutes and found **144** matches (1 before F2, 5 after
  it). Tests: `tests/test_orchestrator.py::test_br3_*`.
- **Review Markdown** is parsed into headings, lists, paragraphs, bold and
  italics, and built with createElement/textContent only (screenshot 33).
  Tests: `test_frontend_wiring.py::test_br4_*`.
- **"none key"** now reads "no key needed" (also "your key", "shared key";
  screenshot 36). Test: `test_br5_key_source_label_reads_as_words`.
- **Source column** uses the same label as the search results, and reference
  items now carry `journal_or_server` (screenshot 32). Tests: `test_br6_*`.
- **Species:** "Human studies only" and "Exclude animal studies" also drop a
  paper whose title names an animal study, for every source; the terms are
  `species.animal_title_terms` in `filter_vocabulary.yaml`. The QA gate
  (`2026-09-29_biorxiv-budget-qa-gate.md`, F1) showed single organism words
  dropping human studies ("bovine serum albumin", "Chinese hamster ovary
  cells", "murine typhus", MICE). The re-gate then showed the first fix
  dropping too much ("mouse", "rat" removed). The list is now lab-animal
  words (mouse, rat, rodent, hamster, macaque, zebrafish, …) plus "<animal>
  model" phrases for names with other senses (bovine, porcine, rabbit,
  canine, equine, murine); exception phrases ("mouse tracking", "Chinese
  hamster ovary", "rat-bite fever", "rat-tail collagen", "mouse-human
  chimeric") are taken out first, a hyphen and a space counting the same; an
  animal word after a reagent prefix (`animal_title_reagent_prefixes`:
  "anti-mouse antibody") is taken out too, while "knockout-mouse model" still
  counts; a word in capitals is an acronym (re-gates 2–3, F4–F8).
  Known gap, stated in the file and a test: veterinary titles such as
  "Bovine mastitis" are not caught. Tests: `tests/test_filtering.py::test_br7_*`.
- **Stored summaries with tags:** a startup migration cleans titles,
  abstracts and abstract-only summaries that contain a known tag, and leaves
  all other text and model summaries alone. Test:
  `tests/test_db_migrations.py::test_br8_stored_markup_cleaned_once`.

Found while checking these:
- **PubMed titles still showed `<i>…</i>`**: PubMed sends the tags escaped
  (`&lt;i&gt;`), and the helper decoded entities after removing tags. It now
  decodes first; the migration also picks up escaped rows. 179 PubMed results
  checked, none with tags (screenshot 37). Test:
  `tests/test_markup.py::test_br2_escaped_tags_from_pubmed_are_removed`.
- A PubMed paper with no journal was labelled " (PubMed)"; now "PubMed".

## Still open

- "Save all" and "Save as…" use the browser's `prompt()`; the run answered it
  with a stub (`window.prompt` replaced in the page) because the test browser
  cannot type into native dialogs. Not a defect.
- A search that includes bioRxiv/medRxiv now takes about 3 minutes for two
  weeks, because every paper in the window is read.

## Checked on production (same day)

A bioRxiv/medRxiv-only search for "inflammation", 14 days, on
https://biorx-production.up.railway.app: all 5,095 papers read in about 3½
minutes, 149 matches, no page limit reached, no errors; titles free of tags.
Two things found and fixed:

- **22 papers read were not accounted for** (149 + 4,924 of 5,095). A match
  that repeats a paper already read was merged and not counted. Now counted
  and shown; the log warns when the totals do not add up.
  Test: `tests/test_orchestrator.py::test_br10_every_paper_read_is_accounted_for`.
- **The page scrolled sideways at 385 px** once results were shown (the
  phone check above ran with none). Tables are in `.table-scroll` boxes;
  checked at 375 px with the 149 results: page 375 wide (586 without).
  Test: `tests/web/test_frontend_wiring.py::test_br11_every_table_scrolls_inside_its_card`.
