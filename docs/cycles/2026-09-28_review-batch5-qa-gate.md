# QA Gate — Front end + admission fixes (review batch 5)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — one COLD pass (a fresh subagent
  given only the diff, the range, and the three catalogue files, with no knowledge
  of how the code was written or of prior passes), plus independent verification
  by the gate author.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1515 passed, 1 skipped**
  (67.60 s; 4 warnings, all Starlette deprecation notices) — up 20 from batch 4's
  1495. No failures.

RANGE:       origin/main..HEAD (caller-supplied; rule 1 of the review-range order).
             origin/main is 0 behind, so the two-dot diff equals the merge-base
             diff. Materialized to /tmp/sweep.diff (1,279 lines) and read
             hunk-by-hunk; changed files then read in full context.
COMMITS:     1 commit:
             fb93066 fix(web): review batch 5 — the page shows and saves what is really there
APPLICABLE:  P1 (transient→terminal in the A6/M16 pollers — an AbortError timeout
             and a discover blip must not end a run that is still going), P2
             (silent drop — the A5 unscoped-key deletion, the reuse path), P5
             (sibling robustness — one `already_running`/`submit_billed` applied
             across summaries/discover/reviews), P6 (derived status — the reuse
             decision made at admission vs re-made in the worker), P8 (stale
             second-run state — A4/A7 restoring a running filter/summary on a
             reload), P19 (producer/consumer drift — the server job key vs the
             client `serverSummaryJob`/`paperKey` lookup, and the node harness's
             regex extraction of app.js functions), P22 (return-contract change —
             the 409 now carries a `payload.job_id` the client adopts), P30
             (hidden-vs-released — the request-thread DB connection).
CHECKED:     P1, P2, P5, P6, P8, P19, P22, P30. Read every changed source file in
             full (jobs.py, spend.py, routes_summaries.py, routes_discover.py,
             routes_reviews.py, routes_searches.py, db.py, summarize.py,
             app.js) and the changed tests. Traced the admission path end-to-end
             (already_running → _resolve_for/admit → submit_billed → worker
             BilledCall), the NO_SPEND reuse path, the A5 localStorage scoping
             (userScoped/dropUnscopedKeys/clearLocalSettings), the A6 poller
             split (pollSearch/pollSearchFor), and the M14–M17 guards. Ran the
             full suite green.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness, concurrency/races (incl. the already_running
             check-then-submit TOCTOU, which `submit`'s own key dedup backstops),
             auth/authz (the new /api/jobs/running endpoint was reviewed for
             shape and ownership, not security), injection & security,
             performance, dependency/supply-chain risk, API-contract
             compatibility beyond P19/P22, test quality, architecture. The
             front-end tests are node-run source-text regex assertions over
             app.js (P19-corollary territory); that is a test-quality property,
             not scored here. Live provider behaviour is exercised by fixtures,
             not live APIs.

---

## The change is Batch 5 of the plan, and it delivers

Every plan ID (A3, A4, A5, A6, A7 page side, M13–M17, A2 page side) is
implemented and pinned by a same-named test asserting exact values, and the two
batch-4 gate findings are both addressed.

- **A3 (new filter resets facets).** `newFilter` resets all six `FACET_FIELDS`
  to `(any)`, so a new filter no longer inherits the previous filter's category,
  paper type, licence etc. Pinned by `test_a3_new_filter_resets_facets` (asserts
  no facet key survives into `buildFilterDict()`).
- **A4 (tab return keeps filter sources).** `loadFilterTab` now redraws defaults
  only when no filter is open; otherwise it re-`selectFilter`s the active id, so
  the open filter's `source_selection` no longer gets replaced by defaults on the
  next Save. Pinned by `test_a4_tab_return_keeps_filter_sources`.
- **A5 (key belongs to one account).** `localSettings`/`saveLocalSettings`/
  `clearLocalSettings` now key storage by `state.me.user_id` via `userScoped()`;
  legacy unscoped keys are deleted (not migrated) by `dropUnscopedKeys()` on
  `showApp`; `signOut` and the 401 gate clear the key before reload. Pinned by
  `test_a5_key_scoped_to_user`, `test_a5_sign_out_clears_key`,
  `test_a5_unscoped_legacy_key_is_dropped_not_migrated`,
  `test_a5_sign_out_and_401_call_clear`.
- **A6 (one poll request at a time, keyed to its job).** `pollSearch`/
  `pollFilterTest` split into a busy-flagged wrapper and a `...For(jobId)` half
  that drops a reply whose job id no longer matches `state.jobId`. `api()` gains
  an `AbortController` timeout (`API_TIMEOUT_MS`), so a request that never
  answers reads as a blip (status 0) rather than hanging a poller, and the
  filter-test button disables while running. Pinned by
  `test_a6_stale_search_response_ignored`, `test_a6_stale_filter_test_response_ignored`,
  `test_a6_api_gives_up_on_a_request_that_never_answers`.
- **A7 (page side) / M13 (busy restored and never double-started).** On boot
  `restoreRunningJobs()` reads `/api/jobs/running` and marks matching Summarize
  buttons busy via `serverSummaryJob`/`isSummarizing`; a 409 on start adopts the
  returned `job_id` instead of billing again; batch summarize adds each paper to
  `state.summarizing` while it runs, so a row click cannot start a second billed
  run. Pinned by `test_a7_busy_state_restored_on_boot`,
  `test_a7_409_adopts_running_job`, `test_m13_row_click_blocked_during_batch`.
- **M14 (stale list/review responses dropped).** `selectRefList` checks
  `state.activeListId !== listId` after the items fetch, and `reviewChecked`
  captures its list id and renders only if that list is still open. Pinned by
  `test_m14_stale_list_response_ignored`.
- **M15 (page errors restore offset).** `turnPage(delta)` restores the previous
  offset and shows a notice on a failed load (e.g. a 410 expired search), wired
  to prev/next. Pinned by `test_m15_page_error_restores_offset`.
- **M16 (discover survives a blip).** `pollDiscover` now counts consecutive
  failures and gives up only through `shouldStopPolling`, resetting the counter
  on success and clearing `discoverPolling` only on terminal status or give-up.
  Pinned by `test_m16_discover_survives_blip`.
- **M17 (delete clears review).** `deleteRefList` hides the review panel and
  download status and resets `refSummaries`. Pinned by
  `test_m17_delete_list_clears_review`.
- **A2 (page side, name clash refused before any request).** Pure
  `filterNameClash(name, filters, ownId)` (trimmed, case-folded, own-id
  excluded) gates both `saveFilter` and `saveFilterAs` before a PUT/POST is
  sent, mirroring the server's 409. Pinned by
  `test_a2_client_blocks_name_clash`.

Specific contracts verified in context, independently of the cold pass:

- **Batch-4 gate finding 1 (duplicate/reuse checks before admission) — fixed.**
  `already_running` (routes_summaries.py:71) now answers 409 before `_resolve_for`
  reserves anything, so at the cap boundary a duplicate returns 409 "already
  running" not 429 "add your own key". The reuse check (`find_paper` →
  `get_summary` → `source_text == "full_text"`) runs before admission and sets
  `resolved = spend.NO_SPEND` (client=None, nothing reserved), so a stored
  full-text summary is returned free even with the allowance exhausted. The
  `finally: ctx.db.release()` in `submit_billed` then covers the reuse path too.
  Pinned by `test_gate4_reuse_at_cap_is_free` (202, reused=True, no model call)
  and `test_gate4_duplicate_at_cap_is_409` (409 with the running job_id).
- **Batch-4 gate finding 2 (request connection release) — partially fixed, see
  finding 1 below.** The `finally: ctx.db.release()` in `submit_billed` closes
  the asymmetry the gate named: the success path and the reuse path now release
  the request thread's connection, not only the 409 path. Pinned by
  `test_gate4_request_connection_released` (asserts the request thread releases
  as well as the worker).

---

## Findings (cold pass, ranked) — dispositioned

1. **P5/P30 · web/routes_summaries.py:114 (same shape in routes_discover.py and
   routes_reviews.py) · the request-connection-release fix is incomplete — the
   admission-failure path and the new review-409 path still hold the connection ·
   confidence: high (severity: low).**
   `finally: ctx.db.release()` lives inside `submit_billed`, which is never
   entered when admission raises. In `start_summary` the request thread acquires
   its connection at `find_paper`/`get_summary` (lines 274–275, both via
   `self.conn`) and again in `_resolve_for` → `spend.admit` → `get_llm_key`/
   `get_user`/`reserve_owner_usage`; when `_resolve_for` raises 400 (no key),
   429 (cap reached) or 503 (provider problem), the exception propagates before
   `submit_billed`, so that connection is never released. `start_review` is
   worse and new: `_get_list_or_404` (line 126) acquires the connection, then
   the newly-added `already_running` (line 129) returns a 409 without any
   release — a path that did not exist before this batch. The comment "give it
   back on every path" overstates: it is false for the admission-failure and
   review-409 paths.
   **Impact is bounded, not a growing leak** — the holder is thread-local, so
   the un-released connection is one per pooled request thread, held for the
   thread's life and reused by the next request on that thread; no data is lost,
   no write lock is held (the 400/429/503 paths do only reads; a successful
   `reserve_owner_usage` proceeds to `submit_billed` and releases), and nothing
   crashes. This is the same class the batch-4 gate already dispositioned as
   "low, small cleanup" — batch 5 closed the primary (success/reuse) half and
   left the failure half.
   **Disposition:** record; fold into Batch 8 cleanup (M34). The complete fix is
   small and localized — a `try/finally: ctx.db.release()` around each route's
   admission segment, or `ctx.db.release()` in `_resolve_for`'s `except` and in
   `start_review` before the 409 return — but it touches the shared admission
   path across three routes and is exactly the kind of change that belongs in a
   scheduled cleanup batch rather than an ad-hoc loop edit (learnings P26).

2. **P6/P22 · web/routes_summaries.py:276-279 · the reuse decision is made once
   at admission and re-derived in the worker, and the two can disagree ·
   confidence: low.**
   Admission sets `resolved = spend.NO_SPEND` (client=None) because the stored
   summary is `source_text == "full_text"`, but `summarize_paper`
   (src/summarize.py:223-234) re-checks the same row inside the worker. If the
   shared summary were no longer full-text when the job runs, `get_client()`
   yields `(None, "")` and `client.summarize_paper(...)` (summarize.py:278)
   raises a bare `AttributeError: 'NoneType' object has no attribute
   'summarize_paper'`, marking the job ERROR with an unhelpful message.
   **Trigger is narrow in practice** — the schema forbids a full-text→abstract
   downgrade (`SummaryDowngradeRefused`) and there is no delete path, so the two
   checks (both `find_paper` → `get_summary`) agree on every reachable state —
   but the coupling is real and a wrong `resolved` flows silently into the
   worker.
   **Disposition:** record. A defensive guard in `_run_summary`'s model path
   (raise a clear "stored summary changed — run again" when `resolved.client is
   None` instead of dereferencing it) would turn a would-be silent
   `AttributeError` into a plain error; it is a one-line hardening for the next
   batch, not a data-path defect.

No medium-or-higher-severity defect was found in the code this batch changed.
The one high-confidence finding is the incomplete request-connection-release fix
(batch-4 finding 2's failure half), and its impact is the same bounded,
held-not-leaked connection the batch-4 gate already classified low; the second
finding is a narrow, schema-protected coupling.

---

## Verdict: APPROVED

No medium-or-higher-severity findings against P1–P31 for the code in
`origin/main..HEAD`; P1, P2, P5, P6, P8, P19, P22, P30 were applicable and
checked. Every Batch-5 plan ID (A3, A4, A5, A6, A7 page side, M13–M17, A2 page
side) is implemented and pinned by a same-named test asserting exact values, and
batch-4 gate finding 1 (duplicate/reuse checks before admission) is fully
closed — a duplicate at the cap returns 409 not 429, and a stored full-text
summary is returned free even with the allowance exhausted. Batch-4 gate finding
2 (request connection release) is closed on its primary half: the success and
reuse paths now release via `submit_billed`'s `finally`, pinned by
`test_gate4_request_connection_released`. The full suite is green
(1515 passed, 1 skipped). The two cold-pass findings are low-severity and
recorded with dispositions (the failure-path half of the connection release and
a new review-409 instance of it, plus a schema-protected reuse-snapshot
coupling); neither loses data, leaks allowance, or crashes, and neither reopens
the gate.
