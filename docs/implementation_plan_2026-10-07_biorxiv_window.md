# Implementation plan — bioRxiv/medRxiv direct read only where it helps (2026-10-07, BW1–BW7)

**Reported:** "the status line reads — Found 585 · Matched 4 — bioRxiv / medRxiv:
3,000 of 589,593 papers read, 0 match so far… it's going to take hours".
**Status:** PLAN — awaiting owner approval. No code written.
**Touches protected retrieval code:** `src/sources/biorxiv_medrxiv.py` (W1.a);
flagged here, drift baseline advanced in the same batch.

---

## 0. What happens today

- bioRxiv's public API (`api.biorxiv.org/details`) cannot search words. It
  lists every paper in a date range, **oldest first**, 30 per request per
  server; the app checks each one locally.
- `sources_config.yaml` `biorxiv_medrxiv.max_pages: 150` stops it after
  ~4,500 papers per server (≈ 2.5 weeks of bioRxiv, 3–4 minutes).
- For a range starting in 2019 (589,593 papers), it reads the **first two or
  three weeks of 2019** and stops with "page limit reached". The rest of the
  range is never read. The running status ("3,000 of 589,593") does not say
  it will stop at ~4,500.
- bioRxiv's website search (the 36,000 count) is not offered as an API and
  refuses scripted requests (403 from this Mac; 429s from Railway). Its count
  matches **any** of the words in full text: Europe PMC's equivalent for
  "prosocial behavior and social cooperation" in bioRxiv preprints is 42,103
  (any word, full text) vs 7 (all four words, title/abstract).
- Europe PMC indexes bioRxiv and medRxiv preprints with word search over any
  range (e.g. `loneliness`, title/abstract, bioRxiv+medRxiv since 2019: 316),
  but lags on the newest: last 14 days, 2,506 bioRxiv preprints in Europe PMC
  vs 3,528 in bioRxiv.

So the direct read is worth doing only for the most recent weeks, which
Europe PMC may not have yet. For anything older it costs minutes and reads
the wrong end of the range.

## 1. Behaviour

One pure rule, `biorxiv_direct_window(start, end, today, max_days, lag_days)`:

| Case | Direct read | Shown to the user |
|---|---|---|
| Range ≤ `max_days` and ends within `lag_days` of today | the whole range (as today) | the status line names the limit: "reads up to about 4,500 papers per server" |
| Range > `max_days`, ends within `lag_days` of today | **only the last `max_days` of the range** (the newest papers) | "bioRxiv/medRxiv: reading only the last 21 days of the range directly; earlier bioRxiv/medRxiv preprints come through Europe PMC." |
| Range ends more than `lag_days` ago | **none** | "bioRxiv/medRxiv: not read directly for this range (its API lists papers oldest first and cannot search words); their preprints come through Europe PMC." |

If Europe PMC is not among the selected sources in the 2nd or 3rd case, the
message adds: "Tick Europe PMC to include them." These are notes, not
failures: no "page limit reached" warning for a range the app chose not to read.

Config in `sources_config.yaml` under `biorxiv_medrxiv` (rule 8):
`max_direct_days: 21` (what `max_pages` covers, per its own comment) and
`europepmc_lag_days: 60` (how far back Europe PMC is assumed complete; the
14-day figure above shows the lag is real, 60 is a margin — owner can change).

## 2. Design

- New pure module `src/sources/biorxiv_window.py` (not in `PROTECTED`):
  `biorxiv_direct_window(...) -> DirectWindow(start, end, mode, note)` where
  mode ∈ {"full", "recent", "skip"}.
- `src/sources/biorxiv_medrxiv.py` (protected, required, flagged): `search()`
  calls it with the filter's dates and its config; uses the returned
  start/end; in "skip" mode returns no records and `has_more = False` without
  any request.
- `web/routes_searches.py` and `agents/monitor.py`: call the same function
  before the search when bioRxiv/medRxiv is selected, and put the note on the
  job (`job.notes`, shown on the page under the progress line) / in the log.
  Same function, so the note and the read cannot disagree.
- Page: shows `job.notes` lines (textContent) under the progress line.

## 3. Acceptance criteria and tests

| ID | Criterion | Test |
|---|---|---|
| BW1 | The rule: full / recent / skip for the three cases, boundaries exact (range of exactly `max_days`; end exactly `lag_days` ago); custom dates and days_back both handled. | `tests/test_biorxiv_window.py::test_bw1_*` (table) |
| BW2 | Adapter, "recent": the requests it sends use the clipped start date (last `max_days`), not the filter's start. | `tests/test_adapters.py::test_bw2_long_range_reads_only_the_newest_days` (fake API records dates) |
| BW3 | Adapter, "skip": zero API requests, no records, `has_more` False; the orchestrator reports no page-limit failure for it. Real-scale (P9): a 2019–2026 range makes 0 requests instead of 300. | `test_bw3_old_range_makes_no_requests`, `tests/test_orchestrator.py::test_bw3_skip_is_not_a_failure` |
| BW4 | Adapter, "full": unchanged from today (existing B7 tests pass unmodified). | existing `tests/test_adapters.py::test_b7_*` |
| BW5 | Web: a search with bioRxiv/medRxiv selected and a long range puts the right note on the job; with Europe PMC unselected the note says to tick it; no note for a short recent range. | `tests/web/test_searches_routes.py::test_bw5_*` |
| BW6 | Page shows the notes, built with textContent. | `tests/web/test_frontend_wiring.py::test_bw6_job_notes_shown` (node-run) |
| BW7 | Config keys present; missing keys warn and use 21 / 60. Drift baseline advanced with the reason; CI green. | `test_bw7_*`; `tests/web/test_no_retrieval_drift.py` |
| BW-L | Live: a 2019–2026 search with bioRxiv/medRxiv ticked finishes without the 3–4 minute bioRxiv read and shows the note; a 14-day search still reads bioRxiv directly. | **Human/browser check** (production, signed in) |

## 4. Build order

BW1 → BW2–BW4 (adapter) → drift baseline → BW5 (route, monitor) → BW6
(page) → BW7 config → docs → full suite → `/chdp` Hermes gate → push → CI → BW-L.

## 5. Owner decisions (defaults chosen)

- **Long recent ranges read the newest `max_days`** rather than nothing: it
  is the part Europe PMC may lack. Cost: up to 3–4 minutes when bioRxiv/medRxiv
  is ticked.
- **`europepmc_lag_days: 60`.** Shorter reads less; longer reads more
  directly for no gain once Europe PMC has caught up.
- **No scraping of biorxiv.org search.** It is not an API, refuses scripted
  requests, and counts any-word full-text matches.

## 6. Not in this plan

- A search hint that a multi-word entry is an exact phrase already exists
  (AND8). The owner's example `prosocial behavior and social cooperation`
  is one phrase (0 results); `prosocial AND cooperati*` finds papers.
