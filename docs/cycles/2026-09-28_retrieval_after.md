# Retrieval after the batch-1 fixes — live run, 2026-09-28

Plan section 11: "B1/B2/B3/B7 against live APIs — before/after live run of the
saved filters". Before: `docs/cycles/2026-09-28_retrieval_baseline.json`.
Command, as before: `agents/monitor.py --filter NAME --dry-run --max 200`,
run from the batch-8 branch with the owner's `filters.json`. Read-only.

## Inflammation (23:21–23:23)

| Source | Before | After |
|---|---|---|
| Europe PMC | 136 | 138 |
| PubMed | 0 | 0 |
| PsyArXiv | skipped — HTTP 400 | 0 (answered) |
| SocArXiv | skipped — HTTP 400 | 0 (answered) |
| bioRxiv/medRxiv | 30 | 60 |
| arXiv | skipped — HTTP 500 | 0 (answered) |
| Unique / matched | 166 / 136 | 198 / 138 |

Every selected source answered; the three HTTP errors (B2: empty dates) are
gone. bioRxiv/medRxiv now reads the filter's whole date window (B7).

**Correction (2026-09-29, browser run):** the bioRxiv/medRxiv "60" was not an
improvement. The API sends 30 papers per call and the adapter stopped after
the first call per server, so it read 60 of about 4,900 papers and said
nothing; every one of the 60 was dropped by the filter. Fixed; see
`docs/cycles/2026-09-29_browser-run.md` F2.
Crossref enrichment was rate-limited for 115 of 130 papers on this run (the
filter had been run three times in a few minutes); the run said so on its
own line and exited 2.

## Loneliness (23:24–23:27)

| Source | Before | After |
|---|---|---|
| Europe PMC | 248 (only source searched) | skipped — Europe PMC returned 503 after 3 attempts |
| PubMed | not searched | skipped — same Europe PMC 503 |
| PsyArXiv | not searched | skipped — api.osf.io read timeouts after 3 attempts |
| SocArXiv | not searched | 20 |
| bioRxiv/medRxiv | not searched | 59 |
| arXiv | not searched | 1 |
| Unique / matched | 248 / 0 | 80 / 0 |

Before the fix only Europe PMC was searched (B1: one budget for the whole
run). After it, every source is asked. On this run Europe PMC and OSF were
down; the outage was retried, reported per source, and the run exited 2. An
earlier run the same evening found 210 unique records and 6 matches; it listed
Europe PMC, PubMed and PsyArXiv as failed without saying why (the message fixed
below), so which of them answered then is not known.

## Found by this run, fixed

The first after-run printed `Sources failed: europepmc, pubmed` without a
reason. monitor.py now prints the reason with each source ("truncated",
"unavailable", …), so a source cut off at `--max` is not reported as failed
(commit "fix(monitor): name why a source was not fully searched").
