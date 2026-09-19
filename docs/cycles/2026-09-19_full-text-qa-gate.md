# Full-text summaries — Learning-QA gate

RANGE: 2c86177..HEAD (origin/main..HEAD, 9 commits)
COMMITS: 02411ab, 40c38fb, a0e5a00, 0122572, b73bcdf, 30bfb3b, 98c107c, 5597e5c, ba0993d
PLAN: docs/implementation_plan_2026-09-19_full_text.md (FT1, FT3, C1–C4)

APPLICABLE (checked): P1, P2, P3, P5, P8 (LEARNINGS SSRF), P9, P10, P19, P21, P25,
P31/P35, P36, P8-dirty-state, P27 (dead-test scan), LEARNINGS P9 (XSS sink).

CHECKED (clean, no finding): SSRF guard on finder-supplied URLs (src/fulltext.py:247-276
routes every download through safe_fetch.fetch_pdf, and https_candidate; test
test_ft3_7_finder_downloads_are_guarded). Outage-vs-absence core: 404 -> "no free copy",
429/5xx -> "unreachable" (src/fulltext.py:146-167, 305-312); test_ft3_6_outage_is_not_absence.
No-model-call on the abstract stand-in (web/routes_summaries.py:214-234) with the owner-usage
slot released (user_store.release_usage, idempotent DELETE) and source_text="abstract" stored.
Abstract rendered via textContent everywhere including renderAbstractStandIn (app.js:1147-1155);
LEARNINGS P9 clean. Strict title matching + first-author/year (title_matches) behaves against
the real CanonicalRecord.to_dict() shape (pub_date and authors-as-string both present). Sibling
HTTP calls share with_retry (P5). Schema migration is additive and idempotent.

NOT COVERED (flagged, not claimed): live calls to OpenAlex / Semantic Scholar / Unpaywall
(plan §3 integration-only); real PDF downloads against live hosts (redirects, http-only hosts);
per-test mutation proof of the new tests (P27) — read for dead asserts, not individually mutated.

VERDICT: REJECTED (one medium-low finding, small fix; everything else low and clean)

---

## FINDINGS (ranked)

### F1 — MEDIUM-LOW · P21 / P25 / P19 — the operator's title-search off-switch is dead on the web
- Where: web/static/app.js:309-311 (`findByTitle()` hardcodes "on unless localStorage says off"),
  app.js:1071 (sends an explicit `find_by_title` on every request), web/app.py:163
  (healthz computes `find_by_title_default` from config — and nothing reads it).
- Failure scenario: operator sets `find_by_title: false` in sources_config.yaml (the documented
  global default). The web client never consults it: the Settings checkbox still renders checked,
  and every POST /api/summaries still sends `find_by_title: true`, so `_extract_text` receives a
  non-None value and never falls back to config (web/routes_summaries.py:132-133). Title search
  runs anyway. Only the desktop/CLI agent honours the flag (agents/summarization_agent.py:83 reads
  config directly), so the two surfaces diverge, and the `find_by_title_default` field added to
  /healthz in this very diff has no consumer (a value built but not wired).
- Fix: in `findByTitle()`, fall back to the server's `health.find_by_title_default` when
  localStorage has no value, so the client default is config-driven and only an explicit user
  toggle overrides it. Then add a frontend-wiring assertion that the fallback exists.
- Confidence: high.

### F2 — LOW · P1 / P35 — permanent 401/403 misclassified as "unreachable (retryable)"
- Where: src/fulltext.py:161-162.
- Failure scenario: `default_get_json` maps 404 -> None ("no free copy") and 429 -> RateLimitedError,
  but every other non-ok status (notably Unpaywall 401/403 for an invalid or unauthorized contact
  email) raises SourceUnavailableError, which the chain records as "could not be reached — try again
  later" (src/fulltext.py:308-312). A permanent config error is reported as a transient outage, so a
  user retries instead of fixing the email. The 404/429/5xx classes are separated correctly; this
  third class collapses into "unreachable".
- Fix: distinguish auth errors (401/403 -> a "rejected by <source>" terminal message pointing at the
  contact email) from genuine unreachable (5xx/timeout/DNS).
- Confidence: medium.

### F3 — LOW · P3 / P2 — "no free copy" recorded when Unpaywall was never asked
- Where: src/fulltext.py:178-179 (returns [] when no DOI) + src/fulltext.py:318 ("no free copy").
- Failure scenario: a paper with an email set but no DOI makes `unpaywall_urls` return []. The chain
  reports "Unpaywall: no free copy" — factually false: Unpaywall is DOI-keyed and cannot search by
  title, so it was never consulted. The no-email case is correctly reported as "skipped", but the
  no-DOI case is mislabelled. Diagnostic accuracy only; the chain proceeds correctly to OpenAlex/
  Semantic Scholar title search.
- Fix: return None (or a distinct "no DOI to ask with" skip) when there is no DOI, mirroring the
  no-email handling.
- Confidence: high.

### F4 — LOW · P36 / P9 — post-cap finder lookups still fire, and their results are discarded
- Where: src/fulltext.py:306-307 (each `find()` HTTP call runs) before the cap check at :323.
- Failure scenario: once `downloads >= max_downloads`, the chain still calls `openalex_urls` /
  `semantic_scholar_urls` (real network GETs, including title searches) for every remaining finder,
  then immediately bails in the download loop and drops the URLs. A capped run therefore makes up to
  4 wasted finder API calls whose results can never be downloaded; an outage in a post-cap finder is
  also still appended as "unreachable" even though it could not have been used. The cap itself is
  correctly announced (P9 satisfied for downloads).
- Fix: break out of the finder loop once the cap is reached (or skip `find()` for the remaining
  finders), and note "skipped (download limit reached)".
- Confidence: medium.

### F5 — LOW · P10 / P8 — the new summaries columns' migration is not itself dirty-state tested
- Where: tests/test_db_concurrency.py:274-292 (`test_migration_is_additive_on_a_populated_db`
  asserts only `papers` rows) and :268-271 (`test_summaries_records_who_created_it` asserts
  created_by_user_id / model_version but not source_text / text_source).
- Failure scenario: the mechanism is proven (ALTER TABLE ADD COLUMN DEFAULT is safe in SQLite, and
  `test_add_column_if_missing_raises_on_a_real_failure` covers failure visibility), but there is no
  test that a populated, old-schema `summaries` table gains source_text/text_source with '' defaults
  for pre-existing rows. A regression that, e.g., dropped the DEFAULT or the column would not be
  caught by the current migration test. Coverage gap, not a defect.
- Fix: extend the populated-db migration test to build an old-schema summaries table with rows and
  assert the new columns appear as '' on those rows.
- Confidence: medium.

### F6 — LOW (informational) — title-in-first-10K-chars window can false-reject a real PDF
- Where: src/fulltext.py:125, 141 (TITLE_WINDOW_CHARS / text_is_this_paper).
- Failure scenario: a legitimate paper whose extracted title appears after a long author/metadata
  preamble (or a publisher page that extracts abstract-first) is rejected as "a different document"
  and silently downgraded to abstract-only. This is the deliberate strictness of commit 5597e5c
  (a wrong-document summary is worse than none), and the fallback is safe, so it is noted as a known
  tradeoff rather than a defect.
- Fix: none required; optionally surface "title not found in the first 10k chars" distinctly from
  "title absent" in the logged reason.
- Confidence: high (as a tradeoff, not a bug).

---

## Test results

- venv/bin/python -m pytest tests/ -q  ->  1081 passed, 1 skipped, 4 warnings.
- /opt/homebrew/bin/pytest tests/ -q  ->  1053 passed, 19 skipped (18 PyQt6 GUI + 1), 4 warnings.

Both green. The single skipped test is environment-dependent (not a failure).
