# QA Gate — Shared summary integrity + one summarize pipeline (review batch 2)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (2 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep (one cold pass via subagent, findings independently verified against source by the gate author)
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1454 passed, 1 skipped** (58.37 s; 4 warnings, all Starlette deprecation notices) — up 36 tests from batch 1's 1418.

RANGE:       origin/main..HEAD (caller-supplied). origin/main is 0 behind, so the
             two-dot diff equals the merge-base diff. Materialized to /tmp/sweep.diff
             (2,149 lines) and audited hunk-by-hunk, then read the changed files in
             context (summarize.py, db.py, llm.py, llm_providers.py, review.py,
             routes_summaries.py, routes_searches.py, arxiv.py, crossref.py,
             schema.py, summarization_agent.py, gui.py).
COMMITS:     2 commits:
             f38d893 test(drift): move the retrieval baseline past review batch 2
             eb2302d fix(summaries): review batch 2 — shared summaries are server-verified, one pipeline
APPLICABLE:  P1 (transient as permanent negative — resolver outage vs not-found,
             Ollama 429), P2 (silent drop — insert_summary returning None, unreadable
             records), P3 (narrow-scope exclusion — lookup_at_source id prefixes),
             P5 (sibling robustness — Ollama retry gate vs hosted clients),
             P19 (producer/consumer drift — summary_text convention A13), P22
             (return-contract change — OllamaClient.generate None→raise), P28/P29
             (test isolation / exact assertions).
CHECKED:     P1, P2, P3, P5, P19, P22, P28, P29. Traced the full summarize path
             (resolve_paper → summarize_paper → _store → insert_summary), the spend
             admission/release bookkeeping in _run_summary (provider_called vs
             recorded), the M27 insert_paper UNIQUE-vs-NOT-NULL branch, the A13
             summary_text convention and its migration, the A11 queue ordering, and
             every new test's exact-value assertions. Confirmed each plan-ID has a
             same-named test and that tests assert exact values (== counts, exact
             strings, exact list equality), not floors.
NOT COVERED: no caller-excluded paths. Out-of-scope families per learning-qa.md:
             logic/algorithmic correctness, concurrency/races, auth/authz,
             injection & security, performance, dependency/supply-chain,
             API-contract compatibility, test quality, architecture. Live provider
             behaviour (how Ollama/Europe PMC/Crossref actually answer) is only
             covered by fixtures, not exercised live.

---

## The change is Batch 2 of the implementation plan, and it delivers

The two blockers the plan named are closed. A1 (the paper's content never comes
from the request body) is real: `start_summary` takes only `paper_ref(body.paper)`
(DOI + canonical_id), and `_run_summary` resolves the paper server-side in the
order stored row → this user's finished search results → source lookup
(`src/summarize.py::resolve_paper`). The adversarial tests pin it:
`test_a1_client_abstract_never_stored` proves an invented abstract is never stored,
`test_a1_client_pdf_url_ignored` proves an attacker `pdf_url` is never fetched, and
`test_a1_resolver_outage_is_retryable_and_writes_nothing` proves a resolver outage
is a loud retryable error (503-style) that writes no row (P1 handled, not just
claimed).

S1 (one pipeline) is real: the web route and the CLI both call
`summarize_paper`, and `test_s1_route_and_cli_store_identical_rows` asserts the two
store byte-identical rows. The A13 convention (`summary_text` empty when structured
fields hold the summary) is enforced at the single writer (`_store`) and read
correctly by `review._text_for`, which now sends `summary_text` only when the
structured fields are empty; the idempotent migration clears old CLI
"KEY FINDINGS: …" rows. M26 (a paid full-text summary is never replaced by an
abstract) holds both at the reuse check in `summarize_paper` and as defence-in-depth
in `insert_summary` (`SummaryDowngradeRefused`). A8 (a write that fails is reported,
never "done") is real: `insert_summary` raises `SummaryNotSaved` instead of
returning None, and the route surfaces `saved: false` + "Running it again will call
the model again", with the CLI exiting non-zero. M27/M28/A11/A12/M25 are each pinned
by an exact-value test.

## Findings (ranked)

1. **BLOCKING · P5 (sibling robustness) · src/llm.py:103 · high.**
   The Ollama retry gate tests `if status is not None and status < 500`, so a 408,
   409, or 429 reply becomes a terminal `ProviderResponseError` with **no retry**,
   while its sibling clients (`src/llm_providers.py:46`) retry exactly those statuses
   via `RETRYABLE_STATUS = (408, 409, 429, 500, 502, 503, 504)`. A 429 from a local
   Ollama under load is "not right now" (P1), but this writes it as a permanent bad
   reply. This is the one spot where the batch's own M29/A10 intent — "the same
   parse-or-raise path and retry/backoff as its siblings" — is not achieved.
   **Fix:** import `RETRYABLE_STATUS` in `src/llm.py` and branch on
   `status in RETRYABLE_STATUS` (retry) instead of `status < 500`; keep
   `ProviderResponseError` for genuine client errors (400/401/403/404/422).
   **Regression test:** `test_m29_ollama_429_is_retried_then_unavailable`
   (assert 3 attempts and a `ProviderUnavailableError`, mirroring the existing
   `test_m29_ollama_retried_then_unavailable` for connection errors).

2. **Non-blocking · P22 (return-contract change) · gui.py:1358 · med.**
   `OllamaClient.generate` changed from "returns None/empty on failure" to "raises
   (ProviderUnavailableError / ProviderResponseError)". `DiscoverTermsWorker` still
   calls `resolved.client.generate(prompt)` unguarded and branches on `if not
   result:` — a mid-flight timeout, 4xx, or non-JSON reply now escapes the worker
   thread (dialog hangs, neither `finished` nor `error` fires). This is the known
   retiring-GUI regression the plan already records-not-fixes (the same gap already
   existed for DeepSeek/Anthropic, which always raised). Recorded here per the
   plan's D-decisions; do not fix in this batch.

3. **Non-blocking · P3 (narrow-scope exclusion) · src/summarize.py:85-102 · med.**
   `lookup_at_source` resolves only `arxiv:`, `pmid:`, and DOI. `make_canonical_id`
   (`src/sources/schema.py:161`) also produces `pmcid:{pmcid}` and `title:{digest}`
   ids, so a DOI-less Europe PMC record with only a PMCID, or a DOI-less OSF preprint
   keyed by title fingerprint, resolves as "not found at its source" and cannot be
   summarized via the web route unless already stored or in a recent search result.
   Europe PMC's `get_by_id` already accepts a bare PMCID (`EXT_ID:<id> AND SRC:MED`,
   `src/sources/europepmc.py:196`), so the `pmcid:` case is a trivial add; the
   `title:` case is not reversible from an id alone and should be surfaced as a
   distinct "this kind of id cannot be looked up" rather than a plain 404. Medium;
   not a gate block, but worth a follow-up in a later batch.

4. **Non-blocking note · concurrency · src/db.py:762-792 · (out of scope).**
   The M26 downgrade guard is check-then-insert (SELECT existing `source_text`, then
   `INSERT OR REPLACE`) with no transaction or lock, so two concurrent summarize
   requests for the same paper — one finding full text, one not — can still let the
   abstract stand-in `INSERT OR REPLACE` over a freshly committed full-text summary.
   The guard is defence-in-depth on top of the single-threaded reuse check in
   `summarize_paper`, and concurrency is outside learning-qa's scope, so this is
   recorded, not fixed. A `BEGIN IMMEDIATE` around the guard+insert, or a single
   conditional `UPDATE ... WHERE source_text != 'full_text'`, would close it.

---

## Verdict: REJECTED

1 blocking finding (P5, high), 2 non-blocking findings, 1 out-of-scope note.
The batch's two blocker deliverables (A1 server-verified summaries, S1 one pipeline)
are correctly implemented and pinned by exact-value tests, and the full suite is
green (1454 passed, 1 skipped). But finding 1 is a high-confidence divergence in
the batch's own scope — the Ollama retry gate does not match its sibling clients,
contradicting M29's stated "same retry/backoff as its siblings" — and it is a
one-line fix plus one regression test. Re-sweep the fix commit before approving.
