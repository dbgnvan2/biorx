# Learning-QA gate — batch-h full-sweep re-gate (origin/main..HEAD), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding (P27 / P19-corollary). The F1 fix's *code* is
correct — app.js `boot()` fetches `/healthz` and renders `startup_warnings`, verified
against the actual `notice`/`api` helpers and the `/healthz` route — but its regression
guard is un-failable on the exact seam F1 named. The new test
(`tests/web/test_app.py:157`) asserts two bare substrings (`"/healthz" in src and
"startup_warnings" in src`) that survive the deletion of the *render* line
(`app.js:502`), because both tokens live on the fetch line (500) and the condition
line (501). Deleting `notice("⚠ " + …, "warn")` — the entire point of F1 — leaves the
test green. The repo already holds the right rule in the sibling file
(`_js_without_comments`, `test_frontend_wiring.py:140-150`, whose docstring reads
"Three times now a check … matched the comment that explains it rather than any code");
the fix commit bypassed it. No user-facing defect today — the code works; the defect is
a guard that can rot silently. Suite is green and deterministic: 528 passed, 19 skipped.

---

## Report

RANGE:       `git diff origin/main..HEAD` — caller-supplied range, used exactly
             (step-1 rule #1). Base `origin/main` = `5ffad2e`, HEAD = `098cc65`.
             The base is the merge-base, so two-dot and three-dot are identical.
             Materialized to /tmp/sweep.diff (2,742 lines, 34 files); reproducible
             via the same command. This pass is the re-gate of the full-sweep fix
             commit `098cc65`; its app.js/gui.py/test changes are audited as
             ordinary unreviewed code (P26 corollary).

COMMITS:     29 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, add CI); 18 fix
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, a75924f, febd2fd, 128cef6,
             a55b83e, 49ac308, a928df6, 098cc65: test-qa loop findings, chdp
             F1-F3, contact-email wiring class-wide, then the F1/F3 fix + F2
             deferral); 5 chore (10220a4, 1b0a438, 23e26bc, 065bede, 0d641c7:
             BASELINE advances, W1.a exception).

APPLICABLE:  P2, P5, P19 (corollary), P25, P27, P29

CHECKED:     F1 fix verified end-to-end: `notice` (app.js:56, signature
             `(message, kind="error")`) is type-compatible with
             `notice("⚠ " + …, "warn")`, and `kind="warn"` maps to
             `.banner.warn` (styles.css:62, element `#notice` at index.html:38).
             `api` (app.js:38-54) returns parsed JSON and throws on
             `!response.ok`, so `health.startup_warnings` is a real array and
             `.join(" | ")` is valid. `/healthz` (web/app.py:64-85) returns
             `startup_warnings: list(...)`; `get_orchestrator()` (deps.py:52-65)
             populates them from `orchestrator.warnings`. The boot() fetch is
             correctly nested after `showApp()` and the `notice` element is
             inside the shown region — the app.js fix genuinely works. F3 fix
             (gui.py:2000-2005): logs each warning individually, then one joined
             `showMessage("⚠ " + " | ".join(warnings), 0)` — no overwrite.
             Correct. F2: deferred to TODO.md honestly (documented, not silently
             dropped) — acceptable. New test (test_app.py:147-160) is the finding
             below. P7/P8/P9/P10/P11-P13/P15-P18/P20-P24/P26/P28/P30-P35 not
             applicable to this re-gate's delta.

NOT COVERED: no caller-excluded paths (whole branch). Untracked files not
             reviewed (`.test-qa-report.md`, `Social Baseline Theory.xlsx`,
             `docs/cycles/*-qa-gate.md`, `docs/epigenetic-inheritance-review/`,
             deleted `ARXIV_ADAPTER_TASK.md`). This re-gate's depth was on
             `098cc65`'s delta; the prior full-sweep's findings over the rest of
             the branch stand as reported there. Scope-limit families NOT
             assessed: logic/algorithmic correctness, concurrency/races,
             authn/authz, injection/security, performance, dependency/supply-chain,
             API contract compatibility, architecture. No `./LEARNINGS.md` in this
             repo (re-verified) — the sweep ran against the generic P1–P35
             catalogue only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (528 passed, 19 skipped, 2 warnings, 7.42 s). Deterministic; judged by
             exit code (P24), no flake observed.

---

## FINDINGS

F1 · P27 / P19-corollary · tests/web/test_app.py:157 · HIGH (high confidence)
  `test_h_app_js_reads_healthz_startup_warnings_on_boot` asserts bare substrings
  `"/healthz" in src and "startup_warnings" in src` over app.js text. Both tokens
  live on the fetch line (app.js:500) and the condition line (app.js:501), so
  deleting the render call (app.js:502, `notice("⚠ " + …, "warn")`) — the exact F1
  defect, "fetched but never rendered" — leaves the test green. The two tokens are
  asserted independently, so they could sit in unrelated statements; the assertion
  can also be satisfied by a comment or a longer-string prefix. The docstring's
  "A code-removal/rename would break this assertion" is the P27 "reasoned-about,
  not run" overclaim. The sibling file already has the correct helper —
  `_js_without_comments()` (test_frontend_wiring.py:140-150) strips comments
  precisely because this has bitten three times before — but this test re-reads
  app.js raw and bypasses it. Fix: assert the *render*, not the token — strip
  comments, then anchor a match to the `notice("⚠ " + … startup_warnings …)` call
  (or assert the joined banner line survives in the no-comments source), and run
  the mutation (delete app.js:502 → red) to prove it fails.

---

## Verdict note

1 finding — 1 high. Not clean. The F1 fetch half is now genuinely guarded by
`test_the_client_calls_the_endpoints_that_matter` (test_frontend_wiring.py:123-135,
an exact endpoint-set assertion that fails if `/healthz` disappears from the
client), and the app.js render code itself is correct; the reject is on the render
half's guard, which is a bare-substring source-text test that cannot fail when the
display line is removed. This is the same class the batch has been chasing
(P27/P19), and the fix is a small test change: strip comments, assert the render
call, mutation-prove it. Fix F1 and re-gate before merge.
