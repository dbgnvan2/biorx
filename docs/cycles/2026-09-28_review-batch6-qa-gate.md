# QA Gate — Bounding hostile input + dependencies (review batch 6)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (2 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — one COLD pass (a fresh subagent
  given only the diff, the range, and the three catalogue files, with no knowledge
  of how the code was written or of prior passes), plus independent verification
  by the gate author.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1535 passed, 2 skipped**
  (69.6 s; 4 warnings, all Starlette deprecation notices) — up 20 passed / 1 skipped
  from batch 5's 1515/1. No failures.

RANGE:       origin/main..HEAD (caller-supplied; rule 1 of the review-range order).
             origin/main is 0 behind HEAD's upstream, so the two-dot diff equals the
             merge-base diff. Materialized to /tmp/sweep-batch6.diff (2,458 lines)
             and read hunk-by-hunk; the ~1,200 mechanical hash lines of
             requirements-web.lock were skipped, its provenance header and the four
             version bumps noted. Changed source files read in full context.
COMMITS:     2 commits:
             8d8b92d fix(security): review batch 6 — hostile input is bounded; dependencies patched
             0618c9d fix(deps): batch 6 follow-up — patch the four packages pip-audit flagged
APPLICABLE:  P1 (transient→terminal — monitor download failures; the identity
             encoding vs pre-compressed PDFs), P2 (silent drop — extraction failure
             surfaced as NoText), P4/P9 (hardcoded caps — SCRAPE_MAX_CHARS, the
             page/char/time/memory limits), P5 (sibling robustness — the
             connection-release hardening applied to 3 routes), P19
             (producer/consumer drift — the lock regen command), P22 (return-contract
             change — get_client raising instead of None), P30 (hidden-vs-released —
             the request-thread DB connection). Project P8 (SSRF) and P9 (XSS) are
             touched only through the pre-existing safe_fetch/https_candidate guard.
CHECKED:     P1, P2, P4, P5, P9, P19, P22, P30. Read every changed source file in full
             (paper_meta.py, fulltext.py, pdf_extract.py, pdf_handler.py,
             safe_fetch.py, monitor.py, routes_summaries.py, routes_discover.py,
             routes_reviews.py) and the changed tests. Traced: the B8 `_scan_html`
             linear scanner and `_abstract_from_html` (cap + JSON-LD→meta→div order);
             the B9 child-process extraction path (extract_limits →
             extract_text_limited → _extract, timeout/RLIMIT_AS/returncode mapping);
             the M24 monitor download via safe_fetch.fetch_pdf + .part rename; the M23
             PDFHandler .part rename; the batch-5 findings' fixes
             (releases_request_connection decorator and the get_client() guard). Ran
             the full suite green.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic correctness
             of the `_scan_html` parser beyond linearity; concurrency/races;
             auth/authz; injection & security classes (SSRF/XSS/decompression-bomb
             guards assessed only for data-path effect); dependency & supply-chain
             (the lock hashes and four version bumps); API-contract compatibility
             beyond P19/P22; test quality. The B10 `pip-audit` re-run and the
             fresh-venv `--require-hashes` install could not be re-executed in this
             session (see B10 note below).

---

## What the batch delivers

Every plan ID for batch 6 (B8, B9, B10, M24, M23) is implemented and pinned by a
same-named test asserting exact values, and the two batch-5 gate findings are both
addressed.

- **B8 (hostile HTML cannot stall the server).** `scrape_abstract_from_url` now
  routes through `_abstract_from_html`, which slices the page to
  `SCRAPE_MAX_CHARS = 512_000` and walks it once with `_scan_html` (index every
  `<` and `>` with bisect, one `page.lower()` up front, a `_MAX_TAG_CHARS` bound
  per tag) instead of the old per-page regexes that backtracked quadratically on
  unclosed tags (measured 67 s for 320 KB on Python 3.12.3). Pinned by
  `test_b8_adversarial_html_is_linear` (5 MB of unclosed tags < 5 s),
  `test_b8_scanner_is_linear_without_the_cap` (parametrised over unclosed /
  scripts / deep-nesting), and `test_b8_parser_finds_each_kind_of_abstract`
  (JSON-LD, meta, div/section, plus the too-short → "" case).
- **B9 (untrusted PDF read in a bounded child process).** New `src/pdf_extract.py`
  runs pdfplumber in a subprocess under max_pages, a max_chars budget, a wall-clock
  timeout, and (Linux only) `RLIMIT_AS`. `extract_limits()` reads
  `sources_config.yaml full_text:` (`max_pages`, `extract_timeout_seconds`,
  `extract_memory_mb`) plus the prompt text budget. Pinned by
  `test_b9_max_pages_enforced`, `test_b9_stops_at_the_text_budget`,
  `test_b9_extraction_timeout` (stubbed worker), `test_b9_download_reports_the_limit_as_no_text`,
  `test_b9_limits_from_config`, and `test_b9_memory_limit_linux` (skipped on macOS,
  runs in CI, with a skip reason saying so).
- **B10 (dependencies re-pinned, hashed lock).** pytest/httpx move to
  `requirements-test.txt` (out of the image); `requirements-web.lock` pins every
  package transitively with hashes and the Dockerfile installs it with
  `--require-hashes`. `safe_fetch.pinned_get` sends `Accept-Encoding: identity` and
  `decode_content=False` so the byte cap counts what arrives. Pinned by
  `test_b10_pins_match_installed`, `test_b10_test_tools_stay_out_of_the_image`,
  `test_b10_pdfminer_patched`, `test_b10_lock_matches_the_pins_and_is_hashed`,
  `test_b10_identity_encoding`, and the amended `test_dockerfile_installs_web_requirements_not_pyqt`.
- **M24 (monitor downloads through the shared guard).** `monitor.download_pdf`
  calls `safe_fetch.fetch_pdf(https_candidate(...))` and treats
  `NotAPdf`/`TooLarge`/`FetchRefused`/`FetchFailed` as `"fail"`; writes to `.part`
  then renames. Pinned by `test_m24_html_not_saved_as_pdf` and
  `test_m24_download_is_guarded_and_saved`.
- **M23 (partial download not kept).** `PDFHandler.download_pdf` writes to `.part`
  and renames on completion, so an interrupted stream leaves no `.pdf`. Pinned by
  `test_m23_partial_download_not_kept` and `test_m23_complete_download_is_kept`.

**Batch-5 gate findings — both closed:**

- **Finding 1 (incomplete request-connection release) — closed on the named paths.**
  The new `releases_request_connection` decorator wraps the three billed routes
  (summaries, discover, reviews) and releases `ctx.db` on every exit — success,
  409, and admission failure — closing the admission-failure and review-409 paths
  that batch-5 named. Pinned by
  `test_gate5_connection_released_when_admission_fails` (429 still releases) and
  the existing `test_gate4_request_connection_released`.
- **Finding 2 (reuse decision re-derived in the worker) — closed.** `_run_summary`
  now wraps the client lookup in `get_client()`, which raises `ProviderResponseError`
  ("The stored summary for this paper changed while this ran — run Summarize
  again.") instead of dereferencing `None` when the admitted reuse finds no stored
  full-text summary. Pinned by `test_gate5_reuse_that_finds_nothing_says_so`
  (job ERROR, "changed while this ran" present, "AttributeError" absent).

---

## B10 human check — verified as far as this session permits

The four packages the pre-patch lock was flagged for are now pinned to patched
versions, confirmed independently against the OSV/NVD databases:

| Package | Flagged | Now pinned | Advisory | Status |
|---|---|---|---|---|
| python-dotenv | 1.0.0 | **1.2.2** | CVE-2026-28684 (symlink-follow overwrite) | fixed in 1.2.2 |
| urllib3 | 2.6.3 | **2.7.0** | CVE-2026-44432 / CVE-2026-9375 (decompression bomb) | fixed in 2.7.0 |
| requests | 2.32.5 | **2.33.0** | GHSA-qccp-gfcp-xxvc (redirect header stripping) | fixed |
| cryptography | 46.0.6 | **50.0.0** | (bundled advisories) | current |

`test_b10_pins_match_installed` confirms the pins equal the installed versions;
`test_b10_pdfminer_patched` confirms pdfminer.six ≥ 20251107 in both the lock and
the venv; `test_b10_lock_matches_the_pins_and_is_hashed` confirms the lock pins the
same versions as requirements-web.txt and carries a sha256 hash per entry.

The two remaining human checks — `pip-audit --require-hashes -r requirements-web.lock`
reporting "No known vulnerabilities found", and a fresh Python 3.12 venv installing
the lock with `--require-hashes` — are recorded in commit 0618c9d's message. I could
**not re-execute either in this session**: this CLI runs in single-query (unattended)
mode, and the environment's security gate blocks `pip install` (its package
threat-intelligence lookups to OSV/deps.dev/ecosyste.ms time out, and the refusal
cannot be approved without a user present), plus `curl` to `*.dev` hosts and inline
`python -c`. The independent OSV confirmation above, plus the passing pin/lock/hash
tests, corroborate the human check's conclusion.

---

## Findings (cold pass, ranked) — dispositioned

1. **P5/P30 · web/routes_searches.py:179 (decorator in routes_summaries.py:72-87) ·
   the connection-release hardening is on three billed routes, not the request
   boundary · confidence: medium (severity: low).**
   The `releases_request_connection` decorator releases `ctx.db` for summaries/
   discover/reviews only. Every other route that touches `ctx.db` on the request
   thread (`start_search` via `user_store.get_filter`, filters, session, references,
   usage, `lookup_summary`/`stored_summary`) never releases, and `get_context`
   (auth.py:128) is a plain function with no teardown, so pooled uvicorn threads
   hold their SQLite connection between requests.
   **Impact is bounded, not a growing leak** — `Database.conn` is thread-local, so
   this is one connection per pooled thread (a file descriptor, idle, no open
   transaction, no write lock under WAL); it is reclaimed when the thread exits or
   the process ends. No data loss, no crash, no lock held. This is the same class
   batch-4 and batch-5 already dispositioned as "low, small cleanup — defer to
   Batch 8 (M34)"; batch 6 closes the specific admission-failure/review-409 paths
   that were the named finding, and the remaining sibling routes are the
   pre-existing state, unchanged by this batch.
   **Disposition:** record; defer the request-boundary release (a `get_context`
   yield-dependency or middleware that releases on every request exit) to the
   scheduled Batch 8 cleanup, as batch-4/5 already recorded. Not a gate blocker.

2. **P4/P9 · src/paper_meta.py:129 (`SCRAPE_MAX_CHARS = 512_000`) · the scraped-page
   cap is a hardcoded literal and its truncation is not announced · confidence: low.**
   A publisher page whose abstract sits past 512 KB is silently cut, so "no
   abstract" (truncated) is indistinguishable from "no abstract" (absent). The cap
   itself is per-spec (B8: "input to any remaining regex is capped at 512 KB") and
   abstracts sit near the top of a page, so the silent-cut is a corner case.
   **Disposition:** record; move the cap to config and log "read X of Y chars" when
   truncation fires — a small hardening for a later cleanup, below the loop's
   fix bar.

3. **P19 · Dockerfile:28-30 vs requirements-web.lock:5-8 · the "regenerate with"
   command omits `--no-emit-index-url --no-index` · confidence: low.**
   The Dockerfile's commented regen command
   (`pip-compile --generate-hashes --strip-extras -o requirements-web.lock requirements-web.txt`)
   omits the flags the lock's own provenance header records
   (`--no-emit-index-url --no-index --output-file=... --strip-extras`), so
   regenerating per the comment produces a different lock than the committed one.
   **Disposition:** record; align the comment with the header. Comment-only drift,
   below the fix bar.

4. **P1 · src/safe_fetch.py:185,188 (`Accept-Encoding: identity` + `decode_content=False`)
   · a server that gzips a PDF anyway yields a false NotAPdf · confidence: low.**
   If a server/CDN ignores the identity request and serves a pre-compressed body,
   the compressed bytes fail the PDF-magic check and a genuinely downloadable PDF
   is reported as `NotAPdf`. In practice PDFs are almost never served with
   Content-Encoding (they are already compressed), and decoding would reintroduce
   the decompression-bomb exposure B10 closes — so the trade-off is intentional.
   **Disposition:** record; if a gzip Content-Encoding is ever observed in the
   wild, decode and re-check the cap on the decoded length. Below the fix bar.

No medium-or-higher-severity *defect* was found. The single medium-confidence
finding is the continuation of a class the two prior gates already dispositioned
low and deferred; its impact is bounded and batch 6 closes the specific paths that
were named.

---

## Verdict: APPROVED

No medium-or-higher-severity defect against P1–P30 for the code in
`origin/main..HEAD`; P1, P2, P4, P5, P9, P19, P22, P30 were applicable and checked.
Every batch-6 plan ID (B8, B9, B10, M24, M23) is implemented and pinned by a
same-named test asserting exact values, and both batch-5 gate findings are closed —
admission-failure and review-409 paths now release the request connection
(`test_gate5_connection_released_when_admission_fails`), and an admitted reuse that
finds no stored summary fails with a clear message instead of an AttributeError
(`test_gate5_reuse_that_finds_nothing_says_so`). The dependency re-pin is
independently corroborated against OSV/NVD (python-dotenv 1.2.2, urllib3 2.7.0,
requests 2.33.0, cryptography 50.0.0), and the pin/lock/hash consistency tests pass.
The full suite is green (1535 passed, 2 skipped). The four cold-pass findings are
low-severity and recorded with dispositions; none loses data, leaks an unbounded
resource, or crashes, and none reopens the gate.
