# Implementation plan — clearer "results incomplete" summary, and a per-filter limit (2026-10-08, WS1–WS7, FL1–FL6)

**Request:** "plan the clearer warning summary and actions to take. And it's not
clear that 'limit' can be set outside of an ad hoc search. This should be part
of the FILTER settings."
**Status:** PLAN — awaiting owner approval. No code written.
**Touches protected retrieval code:** `src/sources/orchestrator.py` (WS2: one
optional callback carrying read/total counts; nothing fetched, merged or kept
changes) — W1.a, flagged; drift baseline advanced once.

---

## 0. Today

The owner's search produced one run-on paragraph:

> Europe PMC had more results than Max results allows; only the first ones
> were read — raise Max results or narrow the filter. PubMed had more results
> … PsyArXiv … SocArXiv … bioRxiv / medRxiv stopped responding part-way …
> arXiv … Results are incomplete.

What is missing from it:
- **How far over** each source was. Each adapter already reports its total
  (`last_total`), and the orchestrator puts "N of M read" in the passing status
  line, but the warning (`record_failure` → `job.source_problems`, text from
  `sources_config.yaml` `failure_reasons`) carries only a kind.
- **What to do per source.** The fixes differ: PubMed is part of Europe PMC
  (untick it); PsyArXiv/SocArXiv read the whole date range unless a **Title**
  word lets OSF narrow it (so "Max results" there means papers *read*, not
  matches); bioRxiv/medRxiv "stopped responding" is the Railway block, and its
  preprints come through Europe PMC; Europe PMC/arXiv need narrower terms or a
  higher limit.
- **Where the limit is.** "Max results" sits only in the ad hoc search box:
  - running a saved filter from the Search tab silently uses that box's value;
  - the filter's **Test** button always uses 200 (`routes_filters.test_filter`);
  - `agents/monitor.py` uses its `--max` option (default 200);
  - a saved filter cannot store a limit at all.

## 1. Behaviour

### Warning summary (WS)

Replaces the run-on paragraph with one block above the results:

```
Results are incomplete — 6 sources could not be read in full.
Most useful next step: put your main word in the Title box (PsyArXiv and SocArXiv read every
paper in the date range without one), and untick PubMed (it is part of Europe PMC).

  Europe PMC    read 200 of 2,391 matches   Narrow the words (add AND …, or use Title), or raise
                                            the limit to 2,391 or less.
  PubMed        read 200 of 1,204 matches   Part of Europe PMC — untick it.
  PsyArXiv      read 200 of 5,310 papers    No Title word, so every paper in the date range is
                in the date range           read. Put a key word in the Title box.
  SocArXiv      read 200 of 1,877 papers …  (same)
  bioRxiv/medRxiv  stopped responding       It refuses many requests from this server; its
                                            preprints come through Europe PMC. Untick it.
  arXiv         read 200 of 913 matches     Raise the limit to 913, or narrow the words.

The limit (papers read per source) for this search: 200 — [change it] (ad hoc: the box above;
saved filter: in Filters).
```

Rules (pure function, so the page and tests agree):
- Per source: `read`, `total` (when known), what was counted ("matches" for
  sources that search words; "papers in the date range" for PsyArXiv/SocArXiv
  when no Title word narrowed them, and for bioRxiv/medRxiv), and one action
  from a table in `sources_config.yaml` (editorial text in config, rule 9).
- Actions chosen by situation: PubMed truncated while Europe PMC is selected
  → "untick PubMed"; OSF source with no Title word → "add a Title word";
  outage/refusal kinds → existing reason + "covered by Europe PMC" for
  bioRxiv/medRxiv; total ≤ the ceiling → "raise the limit to N"; total above
  the ceiling → "narrow the words" only.
- The "most useful next step" line picks the highest-impact action present
  (order in config).
- Every line built with createElement/textContent.

### Limit in filter settings (FL)

- The Filters editor gets **"Papers read per source (limit)"**, default 200,
  1–2,000, saved in the filter as `max_results`.
- Running a saved filter (Search tab Run, Filters tab Test, the monitor) uses
  the filter's own limit. The Search tab shows it beside Run: "reads up to 200
  per source — set in Filters".
- The ad hoc box keeps its own limit, relabelled "Papers read per source".
- The default and ceiling move from `web/routes_searches.py` constants to
  `sources_config.yaml` (`search.default_max_results: 200`,
  `search.max_results_ceiling: 2000`) and reach the page through `/api/config`
  (rule 8; the page stops hard-coding 200/2000).
- Old saved filters (no `max_results`) use the default; nothing is migrated.
- Monitor: the filter's limit wins; `--max` given explicitly overrides all
  filters for that run (logged).

## 2. Acceptance criteria and tests

### WS — warning summary

| ID | Criterion | Test |
|---|---|---|
| WS1 | Per-source counts reach the job: `job.source_limits[name] = {read, total, counted}` for every truncated source. | `tests/web/test_searches_routes.py::test_ws1_limits_on_the_job` |
| WS2 | Orchestrator (protected): one optional callback `on_source_limit(name, read, total)` called where truncation is already reported; nothing else changes. | `tests/test_orchestrator.py::test_ws2_limit_callback_carries_counts` (Europe PMC-like adapter, 1,000 total, limit 200 → (200, 1000)); existing truncation tests unchanged |
| WS3 | `limit_summary(job, selection, filter)`: the per-source rows and the next-step line, for the owner's six-source case and for single-source cases; editorial text from config. | `tests/test_limit_summary.py::test_ws3_*` (table-driven, incl. the owner's case) |
| WS4 | Adversarial (P7): PubMed truncated **without** Europe PMC selected is not told to untick; an OSF source **with** a Title word is not told to add one; a total above the ceiling never suggests "raise the limit". | `test_ws4_*` |
| WS5 | The page shows the block (heading, next step, one row per source, the limit and where to change it), textContent only; the old paragraph is gone; a search with no problems shows nothing. | `tests/web/test_frontend_wiring.py::test_ws5_*` (node-run) |
| WS6 | Config: `limit_actions` and the next-step order in `sources_config.yaml`; missing keys warn and fall back. | `test_ws6_*` |
| WS7 | Live: the owner's style of search (broad word, all sources) on a local server shows the block with real totals; screenshot in the status report. | local browser check |

### FL — limit in filter settings

| ID | Criterion | Test |
|---|---|---|
| FL1 | A saved filter stores `max_results`; the editor field reads and writes it; out of range (0, 2,001, text) is refused (400) with the range in the message. | `tests/web/test_filters_routes.py::test_fl1_*`; node-run editor test |
| FL2 | Search tab Run of a saved filter uses the filter's limit, not the ad hoc box. | `tests/web/test_searches_routes.py::test_fl2_saved_filter_uses_its_limit`; node-run `startSearch` test (no `max_results` sent for a saved filter) |
| FL3 | Filters tab Test uses the filter's limit (not a fixed 200). | `tests/web/test_filter_test_route.py::test_fl3_*` |
| FL4 | Monitor uses each filter's limit; an explicit `--max` overrides and is logged. | `tests/test_monitor.py::test_fl4_*` |
| FL5 | Default and ceiling come from `sources_config.yaml` via `/api/config`; the page has no 200/2000 literals for this. | `tests/web/test_app.py::test_fl5_config_exposes_limits`; frontend check that the inputs' max/default are set from config |
| FL6 | The Search tab shows the selected saved filter's limit beside Run, with "set in Filters". | node-run test |
| FL-R | Old filters without `max_results` run with the default; existing filter, search and page tests pass unmodified (except label text where tests pin it — listed in the commit). | full suite |
| FL-L | Live on production after deploy: set a filter's limit to 500, run it, see "read 500 of …". | **Human/browser check** (signed in) |

## 3. Build order

WS2 (orchestrator callback) → WS1 (job) → WS6 config → WS3/WS4 summary
function → FL5 config/API → FL1 (store + editor) → FL2/FL3/FL4 (runs use the
filter's limit) → WS5/FL6 page → docs (CHANGELOG, spec, coverage) → full suite
→ WS7 local browser check → drift baseline → `/chdp` Hermes gate → push → CI.

## 4. Owner decisions (defaults chosen)

- **Name:** "Papers read per source" rather than "Max results" — for
  PsyArXiv/SocArXiv/bioRxiv it limits papers read, not matches.
- **Default 200, ceiling 2,000** (unchanged values, now in config).
- **The summary suggests; it changes nothing by itself.** No auto-untick, no
  automatic re-run with a higher limit.
