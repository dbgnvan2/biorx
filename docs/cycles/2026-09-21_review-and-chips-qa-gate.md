# QA Gate — Review checked papers (M4) + Discover chips, re-gate of the batch (M3/M5)

- **Date:** 2026-09-21
- **Range reviewed:** `04264a7..062f101` (5 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1307 passed, 1 skipped**

RANGE:       04264a7..062f101 (caller-supplied)
COMMITS:     5 commits:
             5c0218d feat(references): Summarize checked, with a cost estimate first (M3, M5)
             6a94154 fix(references): gate findings — one filename function, one empty-subset answer
             acdf2af fix(references): two bugs a real batch run exposed, and DeepSeek's rates
             c9df7db feat(references): Review checked papers, and fix four sibling-drift bugs (M4)
             062f101 feat(filters): click a suggested term to make a filter, right-click to add a group
APPLICABLE:  P5 (sibling calls), P19 (producer/consumer drift), P2 (silent drop),
             P1 (transient-as-terminal), P26 (fix-commit introduces a new bug),
             P10 (hard case untested), P30 (reserve-without-release), P4 (config vs code)
CHECKED:     P1, P2, P4, P5, P10, P19, P26, P30 — read src/review.py, src/tokens.py,
             src/user_store.py, src/llm_providers.py, src/llm_config.py, src/jobs.py,
             src/db.py (summary insert + source_text column), web/routes_reviews.py,
             web/routes_summaries.py, web/routes_usage.py, web/routes_discover.py,
             web/routes_references.py, web/static/app.js end to end; ran the suite;
             proved finding 1 by mutation (a live probe asserted the leak and failed).
NOT COVERED: full race analysis across tabs, auth beyond the route-level check,
             injection, Ollama's generate path beyond the signature check, the two
             prior gate files' ranges.

---

## The five concerns — answered

**(a) Sibling class — SERVER-SIDE SOUND; CLIENT-SIDE ONE REMAINING DRIFT.**
The review's server-side spend settlement (`_run_review`'s `finally`) is
byte-for-byte the same shape as `routes_discover`'s: `provider_called` is set
before `generate()`, and the `finally` does `record_spend` when the model was
called, `release_usage` otherwise, with exception-carried usage recovered in the
`except` and `ctx.db.release()` innermost. Cap admission is the shared
`_resolve_for`. So on the server the third spend path is consistent with its two
siblings — no bypass, no double-record (there is no inline `record_spend` to
double; the settle is only in `finally`), and the slot is given back when the
model never ran. On the client, `reviewChecked` carries the double-submit guard
(`state.batchRunning`) and the confirm-before-spend rule, **but** its poll loop
never gives up on an unreachable server — see finding 2. That is a remaining
instance of the exact class this batch has now rejected for three times.

**(b) Contributor/basis accounting — SOUND.**
`src/db.py` documents `source_text` as only `"full_text"`, `"abstract"`, or `""`
(legacy). The two web writers emit only `"full_text"` and `"abstract"`; a legacy
`""` summary predates the abstract stand-in and is a real full-text model
summary, so `_text_for`'s `"" != "abstract" → FULL_TEXT` mapping is correct, not
an over-claim. The abstract stand-in is read back as `ABSTRACT` (its
`source_text == "abstract"`), an empty summary falls through to the abstract, and
a paper with neither is excluded with a named reason. Every dropped (too-long)
and excluded paper is returned in `left_out` and shown by the UI, so nothing is
omitted silently. `basis_note` counts the *included* set — the papers actually
sent to the model — so a synthesis cannot describe reading more than it did.

**(c) Authorization — SOUND.**
`start_review` and `review_preview` call `_get_list_or_404(ctx, user_id, list_id)`
(ownership-scoped, foreign list → 404); `stored_review` adds
`latest_review(…, user_id, list_id)` with `user_id` in the WHERE clause;
`review_status` goes through `ctx.jobs.lookup(job_id, user_id)`, which reports a
foreign job id as UNKNOWN. `item_ids` only narrows `list_reference_items(list_id)`
on an already-owned list, so a foreign `item_ids` cannot pull in another user's
papers. Pinned by `test_m4a3_reviews_are_per_user` (foreign read → 404, foreign
POST → 404) and `test_m4_review_routes_need_a_session` (401 unauthenticated).

**(d) Free-name guard — SOUND for the chip path; editor buttons unchanged.**
`freeFilterName` lower-cases both sides and appends `(2)`, `(3)`, … via
`nextListName`, and `createFilterFromTerm` runs it against `state.filters` before
the upsert POST. `state.discoverSaving` serialises the saves, so a second click
during a save is dropped, and `reloadFilterList()` refreshes `state.filters`
between saves, so the second click on the same term makes `"term (2)"`, not an
overwrite. `saveTermFilter` reads the name back from the DOM field
`createFilterFromTerm` just wrote, so the POST carries the free name, never a
caller-chosen one. The two editor buttons (`saveFilter` / `saveFilterAs`) still
POST a caller-chosen name and remain the upsert-overwrite hazard — that is
pre-existing and is explicitly recorded in `TODO.md`, so it is not a new finding.

**(e) Nothing spends before confirm — but a request leaks a slot first (finding 1).**
Before `confirmSpend` resolves, only `GET …/review-preview` and
`POST /api/usage/estimate` run; neither calls the model. But the preview is not
side-effect-free: it calls `_resolve_for` (the reserving variant), which inserts a
`usage_events` row for an owner-key user and discards the id without releasing it.
No tokens are billed, but a slot of the daily cap is consumed per preview — so a
read-only GET that precedes the user's confirmation already mutates their
allowance. See finding 1.

---

## Findings (ranked)

**1 · P5/P30 · web/routes_reviews.py:202 · HIGH**
`review_preview` resolves the model with `resolved, _ = _resolve_for(ctx, user_id)`
— the *reserving* variant — and throws the returned `usage_id` away. For an
owner-key user (no personal key stored, owner key configured in `.env`), the
default shared-key deployment, `_resolve_for` calls `reserve_owner_usage`, which
INSERTs a `usage_events` row (`key_source='owner'`, `tokens_counted=0`) that is
never released or finalised. Every "Review checked" click therefore consumes one
slot of the daily allowance before the user even confirms; repeated previews drive
`owner_usage_today` up and will 429 a legitimate review that should still have
room. The whole point of the c9df7db split was that non-reserving callers use
`resolve_credentials` — `routes_usage.estimate` does, but the preview sibling uses
the reserving variant instead, which is the same class of seam the batch has been
rejecting for. The preview also reports the owner's model (or the server-stored
key's model), not the inline localStorage key's model, so a BYO-key user sees the
wrong model name too. *Proved by mutation:* a probe that ran the preview with an
owner key present found one leaked `usage_events` row. The committed suite misses
it because `test_m5a2_the_preview_costs_nothing_and_calls_nothing` runs with no
owner key, so `_resolve_for` raises `NoLLMCredentialError` *before* reserving and
the "0 rows" assertion passes vacuously (P10/P20: the hard path is untested).
*Fix:* call `resolve_credentials` (the preview only needs the model name), and add
a regression test with `DEEPSEEK_API_KEY` set that asserts `usage_events` stays
empty after a preview. Confidence: high.

**2 · P5/P1/P26 · web/static/app.js:2601 · MEDIUM**
`reviewChecked`'s poll treats `status 0` (unreachable server) as a blip and
`continue`s with no counter, no `POLL_GIVE_UP`, and no "Can't reach the server"
warning. `summarizeOnePaper` — fixed in the *same commit* for the prior gate's
finding 3 — counts blips, announces after three, and gives up at `POLL_GIVE_UP`;
the new sibling `reviewChecked` (also added in c9df7db) was not given that
treatment. On a stuck or unreachable server the review becomes a silent
infinite loop, and because the loop never exits, the `setBatchRunning(false)` at
the bottom is never reached, so **both** batch buttons stay disabled until a page
reload. This is precisely the fix-commit pattern (P26): the poll fix was applied
to two of the three poll sites. *Fix:* mirror `summarizeOnePaper` — a blip
counter, an announce at three, a give-up at `POLL_GIVE_UP`, and release
`setBatchRunning(false)` on that path. Confidence: high.

**3 · P5 (reused per-paper helper) · web/static/app.js:2350, 2621 · MEDIUM**
`reviewChecked` feeds `reviewEstimate` into the same `confirmSpend` →
`capWarningText` used by the per-paper batch, but a review is **one** model call
(one reserved slot), not one slot per paper. `capWarningText` compares
`cap_remaining >= est.papers`, so with `cap_remaining = 3` and 10 papers ticked it
tells the user "Only 3 of these 10 can run today on the shared key" — false: the
review needs a single slot and will run all ten. The `spend-go` disable is correct
(only blocks at `cap_remaining === 0`), so the bug is the warning text lying about
what will happen — the same "surface must map to reality" failure the rejection's
finding 1 was about, in the opposite direction (over-warn instead of block).
*Fix:* for a review, the cap message must be a one-slot statement ("you have N
calls left today"), not a per-paper one; add a regression test that a review of
many papers with a small remaining allowance shows no per-paper warning.
Confidence: high.

---

## Why the suite stayed green

Each finding sits in exactly the seam the tests do not exercise. (1) The preview
test runs without an owner key, so the reserving branch never executes and the
"0 rows" assertion is vacuous. (2) The poll-consistency test
(`test_gate3_the_batch_poll_matches_the_single_summary_poll`) compares
`startSummary` and `summarizeOnePaper` only — it never reads `reviewChecked`, which
was added in the same commit and therefore missed the sweep's own check. (3) No
test asserts the review dialog's cap message; `capWarningText` is only tested with
summarize-batch-shaped estimates. This is the same pattern the prior gate noted:
the guards protect the route table and the named happy paths, not the
cross-path contract or the newly added sibling.

---

## Verdict: REJECTED

The four prior findings are fixed and verified (estimate resolves credentials via
the shared `resolve_credentials`; no-credential is answered with
`key_source="missing"` instead of a 500; the batch poll gives up; a
`state.batchRunning` guard is released in a `finally`), and the server-side
accounting, basis/contributor honesty, and authorization of M4 are all sound. But
the weighted sibling class has one remaining instance in the new code (finding 2),
and the new feature adds two more defects of its own: the review preview leaks an
owner-key cap slot on every click in the default deployment (finding 1, HIGH, proven
by mutation), and the confirm dialog misstates how many papers a review can run
(finding 3). The fix commit c9df7db introduced a sibling that missed its own fix and
a caller that used the reserving variant of the resolver it had just split out —
which is the exact failure the split existed to prevent.

Required to flip to APPROVED:
1. `review_preview` must resolve via `resolve_credentials` (never `_resolve_for`),
   with a regression test that sets an owner key and asserts `usage_events` is
   empty after a preview (fails on the current code).
2. `reviewChecked`'s poll must get the same blip-counter / announce / `POLL_GIVE_UP`
   treatment as `summarizeOnePaper`, with `setBatchRunning(false)` released on that
   path, and a test asserting the review poll names `status 0` and gives up.
3. The review's confirm dialog must not apply the per-paper cap warning to a
   one-call operation; add a test that a many-paper review with a small remaining
   allowance shows no "Only N of M can run" warning.
4. Re-run `venv/bin/python -m pytest tests/ -q` green, then re-sweep the fix commits
   as their own range.
