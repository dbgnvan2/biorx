# QA Gate — Jobs and spend (review batch 4)

- **Date:** 2026-09-28
- **Range reviewed:** `origin/main..HEAD` (1 commit, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep — one COLD pass (a fresh subagent
  given only the diff, the range, and the three catalogue files, with no knowledge
  of how the code was written or of prior passes), plus independent verification
  by the gate author.
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1495 passed, 1 skipped**
  (64.93 s; 4 warnings, all Starlette deprecation notices) — up 22 from batch 3's
  1473. No failures.

RANGE:       origin/main..HEAD (caller-supplied; rule 1 of the review-range order).
             origin/main is 0 behind, so the two-dot diff equals the merge-base
             diff. Materialized to /tmp/sweep-batch4.diff (1,755 lines) and read
             hunk-by-hunk; changed files then read in full context.
COMMITS:     1 commit:
             6c409ec fix(jobs): review batch 4 — one spend path, separate job pools, no double runs
APPLICABLE:  P1 (transient/permanent negative — reservation-before-dedup ordering at
             the cap boundary), P2 (silent drop — slot release on every never-ran
             path), P5 (sibling robustness — one settlement path replaces three
             drifted copies), P6 (derived status — the M26 free-reuse "reused"
             flag vs the reserved slot), P12 (refactor-dropped side effect — the
             settlement block moved out of three routes), P19 (producer/consumer
             drift — bulk methods vs the single-row methods they replace), P22
             (return-contract change — submit_billed returns Job or JSONResponse).
CHECKED:     P1, P2, P5, P6, P12, P19, P22. Read every changed source file in full
             (spend.py, jobs.py, db.py, llm_config.py, filtering.py, monitor.py,
             routes_summaries.py, routes_searches.py, routes_reviews.py,
             routes_discover.py, routes_references.py, routes_filters.py,
             routes_usage.py, deps.py) and the changed tests. Traced the
             settlement path end-to-end (admit → reserve_owner_usage → BilledCall
             → record_spend/finalize_usage or release_usage), the job-key dedup in
             JobRegistry.submit, the on_never_ran hook on all three never-ran
             paths (queued-cancel, pool-refusal, JobAlreadyRunning), and the
             bulk-vs-single DB methods. Ran the full suite green.
NOT COVERED: out-of-scope families per learning-qa.md: logic/algorithmic
             correctness, concurrency/races (incl. the reserve-then-dedup window's
             interaction with concurrent requests at the cap, and the
             check-then-act note in reserve_owner_usage itself), auth/authz (the
             new /api/jobs/running endpoint was reviewed for its route-count and
             ownership filter, not for security), injection & security,
             performance, dependency/supply-chain risk, API-contract compatibility
             beyond P19/P22, test quality, architecture. Live provider behaviour
             is exercised by fixtures, not live APIs.

---

## The change is Batch 4 of the plan, and it delivers

Every plan ID (M12, A14, A7, M1, M11) is implemented and pinned by a same-named
test asserting exact values. The theme — "one settlement path, separate job
pools, no double runs" — is visibly applied.

- **M12 (one spend path).** New `src/spend.py`: `admit()` raises domain errors
  (400/429/503) instead of `HTTPException`, and `with BilledCall(...) as call:`
  settles exactly once however the job ends — records spend if the provider was
  reached (taking the usage an exception carries), releases the reserved slot if
  it was not, and always releases the worker's DB connection. The three
  previously-drifted per-route settlement blocks (summaries, discover, reviews)
  are deleted. Verified by import scan: `record_spend(`/`release_usage(` appear
  only in `src/spend.py`; routes call only `spend.release_unused`. Pinned by
  `test_m12_single_settlement_path`, `test_m12_routes_agree_on_status_codes`
  (parametrised across all three routes), `test_m12_billed_call_releases_when_the_model_was_never_called`,
  `test_m12_billed_call_records_the_cost_an_exception_carries`, and the updated
  `test_gate2_both_spending_routes_use_the_shared_recorder` (now asserts
  `spend.BilledCall(` in all three routes and no `record_spend(`/`release_usage(`).
- **A14 (a) — lanes and one search per user.** `JobRegistry` now holds two pools
  (search 2, model 3; sizes from `llm_config.yaml` `jobs:` via `job_lanes()`), a
  per-submit `key`, and expiry on `lookup()` as well as on submit. Searches and
  filter-tests share `SEARCH_KEY=("search",)`, so a second concurrent search is
  refused 409. Pinned by `test_a14_one_search_per_user`,
  `test_a14_model_jobs_not_blocked_by_searches`, `test_a14_expiry_on_lookup`,
  `test_a14_lanes_from_config`.
- **A14 (b) — never-ran release.** `submit(..., on_never_ran=...)` fires when the
  worker sees a job cancelled before start and when the pool refuses the submit;
  `submit_billed` registers `spend.release_unused` there, and releases the slot
  itself on `JobAlreadyRunning`. Pinned by
  `test_a14_cancelled_before_start_releases_slot`,
  `test_a14_submit_failure_releases_slot`, and the end-to-end
  `test_a14_a_summary_cancelled_at_shutdown_gives_its_slot_back` (reserved → 2,
  shutdown → released → 3).
- **A7 (server).** A summary job is keyed by `("paper", doi-or-canonical-id)`, so
  a second Summarize for the same paper while one runs returns 409 carrying the
  running `job_id` and no second slot is spent. New `GET /api/jobs/running`
  lists the user's queued/running jobs (kind, paper ref, list id, job id);
  `PROTECTED_ROUTE_COUNT` bumped 40 → 41. Pinned by `test_a7_duplicate_job_409`
  (asserts `owner_summaries_remaining` stays at 2 across the duplicate) and
  `test_a7_running_jobs_listed`.
- **M1 (review errors read plainly).** `start_review` routes credential errors
  through `spend.admit` (400 no key, 503 provider), so the route no longer lets a
  missing key become a bare 500. `review_status` returns 410 "That review has
  expired — run it again." for an expired job and 404 "No such job." otherwise.
  The "nothing to review" case raises a `NothingToReviewError` domain error (not
  `HTTPException`), so the page shows "None of these papers have a summary…"
  instead of "HTTPException: 400: …". Pinned by `test_m1_no_key_is_400`,
  `test_m1_expired_is_410`, `test_m1_nothing_to_review_reads_plainly`.
- **M11 (bulk summaries, one card).** `Database.summaries_for_papers()` (one `IN`
  query per 500 ids) and `Database.find_paper_ids()` (DOI-first, canonical_id
  fallback, chunked) replace the per-paper `get_summary`/`find_paper` loops in the
  four summary consumers. One `summary_card()` serializer is now used in search
  results, list summaries, and (via the same shape) the PDF export. Pinned by
  `test_m11_summaries_bulk_query_count` (≤ 9 SELECTs for 2,000 results, where the
  old path made ~6,000) and `test_m11_card_identical_across_endpoints` (the search
  card and the list card are byte-identical once `item_id` is dropped).

Specific contracts verified in context, independently of the cold pass:

- **Bulk methods replicate the single-row methods line-for-line (P19).**
  `find_paper_ids` returns DOI-first then canonical_id, `None` for a paper
  neither identifies, in input order — identical to `find_paper`; `summaries_for_papers`
  applies the same `key_findings` `json.loads` and row shape as `get_summary`. No
  contract drift.
- **`BilledCall.__exit__` settles exactly once on every path.** Queued-cancel and
  pool-refusal run `on_never_ran` → `release_unused`; `JobAlreadyRunning` releases
  via `submit_billed`; provider-called records spend (finalizing the reserved row,
  or inserting for a user-key run); provider-never-called releases; an exception
  carrying `usage` has it recovered before recording; `db.release()` runs in the
  `finally` regardless. The `start_summary` reordering — `_resolve_for` now runs
  after the `paper_ref` no-DOI check — is a strict improvement: no slot is
  reserved for an unidentifiable paper, and the removed `release_usage` call is no
  longer needed.
- **`normalised=True` flow.** `without_license` now returns `normalise_filter(f)`
  (so `pre_enrichment` is already canonical), and both `_run_search` and
  `monitor.run_search` pass `normalised=True` to the per-record `filter_papers`
  gate — no re-normalisation per record. The final full filter still runs
  `filter_papers(..., filter_dict)` un-normalised, which is idempotent and correct.

---

## Findings (cold pass, ranked) — none blocking, dispositioned

1. **P1 · web/routes_summaries.py:243-252 (same shape in routes_reviews.py and
   routes_discover.py) · a slot is reserved before the dedup-key check, so at the
   cap boundary a duplicate is answered 429 not 409, and a free reuse is refused ·
   confidence: med (gate author: low-med).**
   `_resolve_for` → `spend.admit` reserves the cap row *before* `submit_billed`
   runs the `("paper", ref)` dedup check, and *before* the worker discovers an M26
   free reuse (`reused=True`, no model call). Consequence, only when the day's cap
   is already exhausted: (a) a duplicate Summarize for a running paper returns 429
   "add your own key" instead of 409 "already running, here is its job id" (the
   message the A7 front end keys on); (b) a Summarize on a paper that already has
   a stored full-text summary returns 429 instead of the free stored result. No
   data is lost and no allowance is leaked: the 409 path releases the transient
   slot (`test_a7_duplicate_job_409` asserts remaining stays 2), and at the cap
   boundary the reservation simply returns `None` before any row is written. The
   reuse half (b) is pre-existing from batch 2's M26, not introduced here; the
   duplicate half (a) is new with A7 but is a message-ordering cosmetic.
   **Disposition:** record as an open risk. The proper fix (reserve the slot
   lazily inside the worker once the call is known to be billed, or run the
   dedup/existing-summary check ahead of `admit`) is a contract change to the
   admission path that belongs in a later batch, not a loop fix in this one — it
   would touch `admit`, the pre-spend estimate, and all three routes, and is
   exactly the kind of fix that introduces new surface (learnings P26).

2. **P5 · web/routes_summaries.py:71-90 (`submit_billed`) · the admission step
   releases the request thread's DB connection on the 409 path but not on the
   success path · confidence: low.**
   On `JobAlreadyRunning` the new code calls `release_unused` → `db.release()`;
   on the success path the connection `_resolve_for` opened (via `get_llm_key` /
   `reserve_owner_usage`) is never released by the handler. `Database.release()`'s
   own docstring names "a request handler" as a place it should be called. Bounded,
   not a growing leak — one connection per pooled request thread, reclaimed by the
   holder's finalizer when the thread eventually exits — and pre-existing: the
   success path did not release before this batch either; batch 4 only made the
   409 path release, which is an improvement, not a regression.
   **Disposition:** record. A request-scoped `try/finally: db.release()` around
   the admission step (or a FastAPI dependency) would close the asymmetry; it is a
   small cleanup, not a data-path defect, and no existing test exercises it.

No medium-or-higher-severity defect was found in the code this batch changed. The
two cold-pass findings are a cap-boundary ordering cosmetic (with a pre-existing
reuse half) and a bounded, pre-existing connection-release asymmetry — neither
loses data, leaks allowance, or crashes, and neither reopens the gate.

---

## Verdict: APPROVED

No medium-or-higher-severity findings against P1–P31 for the code in
`origin/main..HEAD`; P1, P2, P5, P6, P12, P19, P22 were applicable and checked.
Every Batch-4 plan ID (M12, A14, A7, M1, M11) is implemented and pinned by a
same-named test asserting exact values: one settlement path with no
`record_spend`/`release_usage` outside `src/spend.py`, separate search/model job
pools with per-user job keys and 409 on duplicates, slot release on every
never-ran path, plain-domain review errors (400/410), and bulk summary lookups
with one card shape. The bulk DB methods replicate their single-row predecessors
line-for-line, `BilledCall` settles exactly once on every traced path, and the
full suite is green (1495 passed, 1 skipped). The two cold-pass findings are
low/low-medium severity and recorded with dispositions; both are edge-case or
pre-existing and neither blocks the merge.
