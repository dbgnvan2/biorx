# QA Gate — Summarize checked, with a cost estimate first (M3, M5)

- **Date:** 2026-09-21
- **Range reviewed:** `04264a7..acdf2af` (3 commits, caller-supplied)
- **Reviewer:** learning-qa failure-pattern sweep
- **Test suite:** `venv/bin/python -m pytest tests/ -q` → **1283 passed, 1 skipped**

RANGE:       04264a7..acdf2af (caller-supplied)
COMMITS:     3 commits:
             5c0218d feat(references): Summarize checked, with a cost estimate first (M3, M5)
             6a94154 fix(references): gate findings — one filename function, one empty-subset answer
             acdf2af fix(references): two bugs a real batch run exposed, and DeepSeek's rates
APPLICABLE:  P5 (sibling calls), P19 (producer/consumer drift), P2 (silent drop),
             P1 (transient-as-terminal), P4 (config vs code), P22 (stale branch),
             P29 (exact route count), P10 (hard case untested)
CHECKED:     P1, P2, P4, P5, P10, P19, P22, P29 — read src/tokens.py, src/llm_config.py,
             src/llm_providers.py, src/user_store.py, src/db.py, web/routes_usage.py,
             web/routes_summaries.py, web/static/app.js end to end; ran the suite;
             traced every POST /api/summaries and GET /api/usage/estimate caller.
NOT COVERED: logic correctness outside the estimate/batch, full race analysis, auth beyond
             the route-level check, injection, the two prior gate files' ranges.

---

## The five concerns — answered

**(a) Sibling class — SUMMARIZE FLOW, PARTLY INCONSISTENT.**
`summarizeChecked`/`summarizeOnePaper` is a second, hand-maintained copy of the
single-paper `startSummary` sequence (lookup → POST → poll). There are exactly two
producers of POST /api/summaries (startSummary at app.js:1150, summarizeOnePaper at
app.js:2357) — no third caller to share it. The lookup semantics agree (both treat a
stored `source_text === "abstract"` as "re-run", anything else as "already done").
But two things `startSummary` has that the batch copy misses, and one that diverges —
all three are the sibling class. The substantive one (finding 3) is the poll loop's
error handling. The heavier sibling divergence is in the *estimate*'s credential
resolution, not the summarize flow itself — see (b)/finding 1.

**(b) Cap bypass / double-count — ACCOUNTING SOUND; ESTIMATE MISRESOLVES WHO PAYS.**
The batch has no batch endpoint: it POSTs /api/summaries once per paper, so the
owner-key cap is reserved atomically per paper exactly as the single button does.
No bypass. No double-count: reserve inserts one `key_source='owner'` row; success/
billed-failure finalizes it (UPDATE), abstract-only and pre-model failure release it
(DELETE), so one paper is one row, and `owner_usage_today` counts only rows that
survive. **But** the estimate endpoint (`web/routes_usage.py:59-88`, `_user_credentials`
:91-106) resolves "whose key pays" from the **server-stored** key
(`user_store.get_llm_key`) and ignores the localStorage inline key that POST
/api/summaries actually honours via `_resolve_for`'s inline branch
(`web/routes_summaries.py:72-76`). A user whose BYO key lives only in the browser
(the supported `byo_enabled=false` path — `renderMe` renders it explicitly) gets an
estimate resolved to the owner key: wrong payer, wrong cap, wrong model/rate. If the
owner cap is at 0, `$("spend-go").disabled` is set and the user is blocked from
running a batch their own key would pay for. This is finding 1, the rejection.

**(c) Abstract-only detection — SOUND.**
`status.result.source_text === "abstract"` is set by exactly one route path (the
no-full-text stand-in, `routes_summaries.py:238-245`), which returns *before* any
model call; the only other "model did not run" outcomes (no abstract AND no text →
`ProviderResponseError`; pre-model exceptions) end the job in `error`/`cancelled`,
which `summarizeOnePaper` routes to `failures`, not `done`. The model-call path always
sets `source_text="full_text"`, so a run that did call the model cannot be reported
abstract-only, and a run that did not cannot be reported `done`. The skip path treats
a legacy `source_text=""` as "already summarized" (safe, no re-spend) — consistent
with `startSummary`.

**(d) Estimator fallbacks — SOUND, one tiny hole.**
Missing `token_estimate` block, a non-mapping block, and malformed scalars all fall
back per-key (`_settings`/`_setting`); missing/malformed rates return `None` (no
price, never an invented one). All covered by tests. Two residual gaps, both confined
to hand-maintained YAML: `rate_for` validates type but not sign (a negative rate
flows into a negative dollar figure), and `isinstance(True, int)` lets a boolean pass
the `_setting`/`rate_for` guards. Cosmetic-only on an advisory estimate; noted, not a
finding.

**(e) Request-before-confirm — SOUND.**
The only request before `confirmSpend` resolves is the read-only
GET /api/usage/estimate. The POST loop runs after `if (!await confirmSpend(...))
return;`, and `spend-go`/`spend-cancel` are the only resolvers of the promise.
Pinned by `test_m5a3_no_request_leaves_before_the_user_confirms`.

---

## Findings (ranked)

**1 · P5/P19 · web/routes_usage.py:74, 91-106 · MEDIUM**
The estimate's "which key would pay" is a hand-maintained copy of `_resolve_for`'s
credential resolution that omits the inline-key branch. `_user_credentials` reads only
`user_store.get_llm_key` (the server-stored key), so a user whose BYO key exists only
in localStorage (`byo_enabled=false`, a supported mode) is resolved to the owner key.
The dialog then states the wrong payer and a false allowance, computes the token/dollar
range against the wrong model, and — when `cap_remaining === 0` — disables the Go
button and blocks a run the user's own key would pay for. The actual spend path is
correct (POST /api/summaries still bills the inline key); the *surface* lies about it,
which is precisely the "surfaces must map to reality" failure this estimate exists to
prevent. *Fix:* make the estimate resolve the same credentials the batch will send —
either pass the inline provider/model/key-presence from `localSettings()` to the
estimate (query params or a body), or share a `resolve_credentials` helper with
`_resolve_for` instead of a second copy — and add a regression test asserting the
estimate reports `billed_to_owner=false` for a localStorage-only key. Confidence: high
that the divergence exists (traced both paths); medium that it fires in practice
(localStorage-only BYO is a real but minority configuration).

**2 · P5 · web/routes_usage.py:74 · LOW**
`resolve_client` can raise `NoLLMCredentialError`/`ProviderUnavailableError` on a
no-key or misconfigured-provider deployment, and the estimate endpoint catches neither
— it 500s where the sibling POST /api/summaries answers a clean 400/503. The front end
degrades to "Could not work out the cost", so nothing hides, but the two routes answer
the same credential problem differently. *Fix (backlog):* catch and map to a clean
error (or `key_source="missing"`).

**3 · P5/P1 · web/static/app.js:2369-2374 · LOW**
`summarizeOnePaper`'s poll treats only 401/404/410 as fatal and `continue`s on every
other error — including a persistent 500 and a network-down `status 0` — with no
warning and no cap on retries. `startSummary` surfaces any non-0 error immediately and
warns after three `status 0` blips. A stuck server turns the batch into a silent
infinite "Summarizing X of N…" loop where the single button would have reported the
failure. *Fix (backlog):* mirror startSummary — fatal on non-blip statuses, warn after
N blips.

**4 · P5 · web/static/app.js:2278 · LOW**
`summarizeChecked` has no double-submission guard and never disables
`btn-ref-summarize-checked`, while `startSummary` carries a `state.summarizing`
dedup set. A second click mid-run starts a concurrent batch; on a user's own
(uncapped) key the papers still in flight re-run and re-bill. The owner cap absorbs it
for shared-key users, so this bites only the BYO-key path. *Fix (backlog):* disable the
button (or a `state.batchRunning` guard) for the duration, matching startSummary.

---

## Why the suite stayed green

Every estimate test exercises the owner key or a **server-stored** user key
(`user_store.set_llm_key`); none exercises the inline/localStorage path, and no
front-end test asserts the estimate's credential resolution agrees with the batch's.
The gap is exactly the seam the source-scan and behavioural tests were not written to
look at — consistent with the prior gates' observation that the guards protect the
route table and the happy paths, not this cross-path contract.

## Verdict: REJECTED

The cap accounting is sound (no bypass, no double-count), abstract-only detection is
correct and complete, the estimator's fallbacks are safe, and no request leaves before
confirm. But the confirm dialog's central claim — whose key pays, and how much
allowance is left — is computed from a credential source that does not match the one
the batch actually bills, because the estimate re-implements credential resolution as a
hand-maintained copy of `_resolve_for` with the inline-key branch left off. That is the
same sibling class the two prior gates rejected for, weighted again per instruction.
One MEDIUM finding; three LOW findings recorded for the backlog.

Required to flip to APPROVED:
1. Make the estimate resolve the same credentials the batch sends (share the resolution
   or pass the inline key through), so a localStorage-only BYO key reports
   `billed_to_owner=false` — with a regression test that fails on the current code.
2. Re-run `venv/bin/python -m pytest tests/ -q` green, then re-sweep the fix commits as
   their own range.
3. Findings 2-4 go to the backlog (or are fixed); they are not gating.
