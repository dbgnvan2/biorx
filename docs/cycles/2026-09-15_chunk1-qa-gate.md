# Learning-QA sweep — 2026-09-15 chunk 1 (arXiv source + filtering extraction)

## Verdict

**APPROVED** — 0 high-severity findings. 3 medium + 1 low logged as follow-ups.
Not "clean": the change was reviewed against P1–P35 and three medium findings remain.

---

## Report

RANGE:       `93bba33..HEAD` (the four most recent commits, per caller focus).
             Caller also named `origin/main..HEAD`; that span holds 11 commits. The 7
             earlier commits (217cc2d..faf35bf) were NOT re-reviewed in this pass —
             only the four most recent were swept.
COMMITS:     4 commit(s):
             7ca0bc1  chore: add global standards table to CLAUDE.md; add openpyxl dep
             1800101  refactor(filtering): move filter semantics out of gui.py into src/filtering.py
             1d889de  feat(sources): add arXiv adapter, arXiv query builder, and headless monitor CLI
             3bda431  docs: arXiv task spec and approved web-app implementation plan

APPLICABLE:  P1, P2, P3, P4, P5, P6, P7, P8, P12, P19, P22, P25, P28, P32, P34
CHECKED:     P1, P2, P3, P4, P5, P6, P7, P8, P12, P19, P22, P24, P25, P28, P32, P34
NOT COVERED: logic/algorithmic correctness beyond the pattern catalogue; live arXiv API
             behaviour (all adapter tests are mocked — no real export.arxiv.org round-trip);
             GUI widget wiring beyond the filtering aliases (no PyQt6 screen test);
             the 7 pre-4-commit commits in the full unpushed range.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0 (138 passed, 2 skipped).
             Success judged by exit code, not by output scraping (P24).

---

## FINDINGS (ranked; none high)

F3 · P5/P2 · src/sources/query_builder.py:205-208 vs src/filtering.py:108 · med
  The arXiv query builder applies the `authors` filter at query time (`au:"…"` /
  `au:term`), but no other source does — build_europepmc_query and
  build_psyarxiv_query ignore authors and let the client-side filter own them.
  Multi-word author terms become exact-phrase clauses (`au:"Park J"`) that are
  stricter than the client-side substring match (`"park j" in auth_str`), so a
  filter with a multi-word author silently returns a *subset* of results when
  arXiv is selected vs the same filter against Europe PMC/PsyArXiv. This breaks
  the very invariant filtering.py exists to enforce (monitor.py docstring:
  "the same saved filter must not mean different things per front end").
  Fix: drop authors from build_arxiv_query and rely on client-side filtering
  (matching the siblings), or make the query clause match client semantics.
  confidence: medium.

F1 · P1/P5 · src/sources/arxiv.py:136-137, 173-174 · med
  The retry loop (while attempts < max_attempts) only `continue`s on 429.
  A transient 5xx (arXiv intermittently 503s under load) raises
  SourceUnavailableError immediately, and any requests.RequestException
  (timeout, connection reset) raises immediately with no retry. The orchestrator
  then treats the source as terminal ("unavailable — skipped") for the whole
  run. Transient failures are recorded as permanent negatives (P1), and the
  robustness is inconsistent within the adapter itself (429 retried, timeout/5xx
  not — P5). Not worse than the Europe PMC sibling (which retries nothing), but
  the retry machinery is present and stops short of the transient class.
  Fix: retry timeouts and 5xx with backoff before raising SourceUnavailableError.
  confidence: medium.

F2 · P2 · agents/monitor.py:134-136, 233-234 · med
  `download_pdf` swallows every exception to a `logger.debug` and returns False,
  and the caller at main() line 233 discards the return value. With default
  logging (INFO) a failed PDF download requested via `--download-dir` is fully
  silent: the record is emitted but the PDF is missing with no signal
  ("failed" is indistinguishable from "skipped", and nothing is counted).
  Fix: log failures at warning, count them, and print a
  "downloaded X / failed Y" summary to stderr at the end of the run.
  confidence: medium.

F4 · P4/P6 · src/sources/arxiv.py:234-236 (with src/sources/schema.py to_dict "version": "1") · low
  arXiv records are frequently versioned (v2, v3). The adapter extracts
  `arxiv_id_full` but strips and discards the `vN` suffix, and CanonicalRecord.
  to_dict() hardcodes `version: "1"`. The client-side "2+ (revised only)" filter
  can therefore never match an arXiv paper. The version facet silently misbehaves
  for the new source. Pre-existing to_dict limitation, but the arXiv adapter is
  where the version information is available and dropped.
  Fix: surface the arXiv version (or at least record it) so the version facet is
  meaningful for arXiv. confidence: low.

---

## What the change got right (why this is APPROVED, not just "no findings")

- The highest-risk change — extracting filtering out of gui.py — is the best-
  verified part of the diff: a single shared implementation, module-level aliases
  preserving every gui.py call site (P12), and an identity test
  (`gui._filter_papers is filter_papers`) plus a real-artifact test that runs
  every saved filters.json entry through the filter (P19/pitfall-2, P32).
- The extraction also *fixed* a real bug (string-shaped `authors` iterated
  char-by-char, silently matching everything) and pinned it with a regression
  test — the fix is named and its oracle is stated (P32 corollary).
- Withdrawn-paper detection is a documented heuristic, anchored to the notice
  form and the abstract's opening, with drops *counted and logged* rather than
  silent (P2 addressed) and an adversarial "ordinary prose" test (P7).
- The pagination-signal change (`last_page_size` vs `len(raw_records)`) is
  correctly scoped: only the arXiv adapter defines `last_page_size`, so every
  other source falls back to the prior behaviour. Tested explicitly.
- Tests use mocks and explicit fixtures; no default-argument construction of
  production objects (P28); no test executes the real monitor.py entry point
  (P34).

## Follow-up (non-blocking)

Log F1–F4 in LEARNINGS.md (none exists yet — create it). Fix F2 (trivial) and F1
(small) in the next chunk; F3 and F4 are backlog candidates but should be decided
deliberately, not dropped silently.
