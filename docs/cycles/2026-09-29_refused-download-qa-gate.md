# QA Gate — Naming a host that refuses a PDF download (bioRxiv/medRxiv on Railway)

- **Date:** 2026-09-29
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep, run directly by the gate author
  against the materialized diff and the changed files read in full context.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1621 passed, 2 skipped**
  (72.3 s; 4 warnings, all Starlette deprecation notices). No failures. The eight
  `br12` tests for this change run green (9 cases incl. the 403/429 parametrisation).

RANGE:       origin/main..HEAD (caller-supplied; single commit 3ad9819).
COMMITS:     1 commit:
             3ad9819 fix(summaries): name a host that refuses a PDF download; explain bioRxiv/medRxiv
APPLICABLE:  P1 (transient→terminal — the 429 must not become a permanent
             negative), P19 (producer/consumer drift — the refusal reason flowing
             safe_fetch → fulltext → the web route), P5 (sibling robustness — the
             other callers of FetchFailed/NoText). Project P8 (SSRF) is touched
             only through the unchanged safe_fetch guard.
CHECKED:     P1, P5, P19. Read the changed files in full (src/safe_fetch.py,
             src/fulltext.py, web/routes_summaries.py, sources_config.yaml, the
             changed tests) and every other caller of FetchFailed / FetchRefused /
             NoText / find_full_text (web/routes_references.py, agents/monitor.py,
             agents/summarization_agent.py). Traced the hop loop in
             safe_fetch._fetch_public, the exception path in
             fulltext.download_pdf_text, and the FullText.explain note assembly.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness beyond the refusal path; concurrency/races; auth/authz;
             injection & security classes (SSRF guard unchanged and not re-audited);
             dependency & supply-chain; API-contract compatibility beyond P19; test
             quality. The live Railway reproduction (bioRxiv/medRxiv answering 429)
             was not re-run — it is recorded in docs/cycles/2026-09-29_browser-run.md.

---

## What the change does

A 403/429 from a PDF host previously read as `"The host answered 429."`, which on
production (Railway, where bioRxiv/medRxiv refuse every request) read as "try again
later". Now:

- `src/safe_fetch.py` — `FetchFailed` gains `status`/`host` (defaulted, so every
  existing single-arg `raise FetchFailed(...)` still works). The hop loop names the
  refusing host for statuses in `REFUSAL_STATUSES = (403, 429)`.
- `src/fulltext.py` — `NoText` gains `refused_by` (defaulted). `download_pdf_text`
  maps a `FetchFailed` whose status is a refusal into a `NoText` carrying the host;
  `find_full_text` collects refusing hosts and `FullText.explain` appends a
  configured note once per host.
- `web/routes_summaries.py` — passes `full_text.refused_download_notes` into the
  finder; the notes live in `sources_config.yaml`.

---

## Each check, verified

1. **The host named is the final hop's after redirects — confirmed.**
   In `_fetch_public`, `parsed = urllib.parse.urlparse(url)` is re-computed at the
   top of every hop, and a redirect does `url = urljoin(url, location); continue`,
   so when a later hop answers ≥ 400 the `parsed.hostname` is that hop's host, not
   the first URL's. Pinned by `test_br12_host_after_a_redirect_is_the_one_named`
   (doi.org 302 → biorxiv.org 429; asserts `e.value.host == "www.biorxiv.org"`).

2. **403 from other finder paths (Refused for API 401/403) is unaffected — confirmed.**
   `fulltext.Refused` is raised only in `default_get_json` for the JSON APIs
   (Unpaywall/OpenAlex/Semantic Scholar) at 401/403, and is caught separately in
   `find_full_text` (the `except Refused` branch, unchanged). The change touches
   only the `safe_fetch.fetch_pdf` download path and its `NoText` mapping; the API
   path shares no code with it.

3. **A 429 is still not a permanent negative (P1) — confirmed.**
   The refusal only affects the in-memory `FullText` object and the
   `outcome["full_text"]` string produced per run. Nothing is persisted or cached
   from `refused_by`/`refusal_notes`; the next Summarize re-runs `find_full_text`
   and re-tries the download, so the stored abstract stand-in is re-tried exactly
   as before. No new "permanent" state was introduced.

4. **The note is not shown when no refusal happened — confirmed.**
   `FullText.explain` builds `notes` only from `refused_by` (appended solely in the
   `except NoText` branch when `refused_by` is set), so an empty/absent refusal
   yields no note and `explain()` returns just the `tried` line. Pinned by
   `test_br12_no_note_without_a_refusal_or_a_configured_host`.

5. **Other callers still work — confirmed.**
   `FetchFailed(message="", status=None, host="")` and
   `NoText(message="", refused_by="")` are backward-compatible; every existing
   caller passes a single string (message). `web/routes_references.py` (PDF proxy)
   and `agents/monitor.py` consume `FetchFailed` via `str(exc)`, unaffected. The
   proxy's 502 detail for a refusal changes wording (host now named) but stays a
   502 — a message improvement, not a contract change.

6. **Config lookups are case-safe — confirmed.**
   `find_full_text` lowercases the note keys (`str(k).lower()`) and appends
   `refused_by` lowercased (`e.refused_by.lower()`); `urlparse().hostname` also
   lowercases, so both sides of the `refusal_notes[h]` lookup agree regardless of
   input case.

---

## Findings

None at medium or higher severity.

- **Non-blocking · P19 · agents/summarization_agent.py:93 — the CLI/desktop finder
  does not pass `refusal_notes`.** It still names a refusing host (via the NoText
  message), but shows no "copy of the app on your own computer" note. This is
  correct: the CLI runs on the user's own computer where bioRxiv/medRxiv do not
  refuse, so the note would be wrong there. Recorded, not a defect.

The three applicable patterns (P1, P5, P19) are all satisfied, and the full suite
is green with exact-value assertions on every `br12` test.

---

## Verdict: APPROVED
