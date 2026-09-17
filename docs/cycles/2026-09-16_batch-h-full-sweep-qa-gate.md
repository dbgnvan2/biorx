# Learning-QA gate — batch-h full branch (origin/main..HEAD), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding (P25) leaves the batch's headline deliverable —
contact-email hygiene — transparent on the search path but invisible on the primary
(web) front end: `startup_warnings` is exposed on `/healthz` and rendered by the
retiring GUI, yet `web/static/` contains zero reads of it, so a web-only user runs
degraded (Unpaywall off, bare `biorx/1.0` UA) with no signal. The test added for this
(`tests/web/test_app.py:130`) asserts only that the JSON key is present — exactly the
P25 "proven one layer below the surface" shape — so the suite reads as wired while the
surface a user touches is not. Two medium findings compound it: the no-contact-email
warning is emitted only in the orchestrator, not on `paper_meta`'s abstract-recovery
paths (an incomplete P5 class fix, with dead `try/except` wrappers around a loader
that never raises), and the GUI status bar overwrites each warning with the next so
only the last survives. No data-loss or correctness defect; the suite is green and
deterministic.

---

## Report

RANGE:       `git diff origin/main..HEAD` — selected by step-1 rule #1
             (caller-supplied range, used exactly). Base `origin/main` = `5ffad2e`,
             an ancestor of HEAD `a928df6`, so two-dot and three-dot are identical
             here. Materialized to /tmp/sweep.diff (2,692 lines, 32 files,
             +2084/-74); reproducible via the same command.

COMMITS:     28 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, add CI); 17 fix
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, a75924f, febd2fd, 128cef6,
             a55b83e, 49ac308, a928df6: test-qa loop findings, chdp F1-F3,
             then contact-email wiring completed class-wide across all seven
             search adapters + Crossref + Unpaywall + BioRxivAPI + SearchAgent.api
             + paper_meta); 5 chore (10220a4, 1b0a438, 23e26bc, 065bede, 0d641c7:
             BASELINE advances, W1.a exception).

APPLICABLE:  P1, P2, P3, P4, P5, P6, P14, P19, P21, P24, P25, P27, P28, P31, P34, P35

CHECKED:     P1 (transient-as-permanent: Unpaywall 429 still raises, not a negative),
             P2 (silent drop: Unpaywall-off and no-email conditions; GUI status-bar
             overwrite; orchestrator warnings list), P3 (narrow scope: source
             enumeration, email-scan dirs), P4 (hardcoded constants: `~/preprints`
             literals, contact-email extraction, placeholder defaults), P5 (sibling
             robustness: all seven search adapters + Crossref/OpenAlex/arXiv/Unpaywall
             polite-pool call sites, and the paper_meta recovery paths), P6 (status vs
             artifact: warnings reflect real missing email; fingerprint guard), P14
             (error-as-content: `""` return on failed abstract fetch), P19 (source-text
             assertions: AST-parse not substring; guard-the-guard; baseline drift),
             P21 (built-not-wired: SearchCache zero callers — documented, not claimed
             integrated), P24 (false-green parse: test gate judged by exit code),
             P25 (front-end wiring: GUI passes warnings; web app exposes on /healthz
             but front end never reads — F1), P27 (fail-able tests: git-fail vs skip,
             mutation-provable), P28 (default-arg production reach:
             BIORX_DB_PATH/BIORX_CACHE_PATH redirect + fingerprint), P31 (absence over
             narrowed population: email scan scope), P34 (entry-point side effects: GUI
             test mocks Database/SummarizationAgent/filters/load_filters), P35
             (unproven baseline: drift-test git failure fails, not skips).
             P7/P8/P9/P10/P11-P13/P15-P18/P20/P22/P23/P26/P29/P30/P32/P33 not
             applicable: no scoring/ranking, LLM judge, background-worker,
             graph/traversal, or return-contract-change code in this range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked files
             not reviewed (`git diff` only): .test-qa-report.md, Social Baseline
             Theory.xlsx, docs/cycles/*-qa-gate.md, docs/epigenetic-inheritance-review/,
             and the deleted ARXIV_ADAPTER_TASK.md (unstaged deletion). Docs-only
             changes reviewed only for personal-address leakage, not content.
             Scope-limit families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API contract compatibility, architecture, and
             test quality beyond P27/P28/P29/P32. This repo has NO ./LEARNINGS.md
             (re-verified) — the sweep ran against the generic P1–P35 catalogue in
             ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (527 passed, 19 skipped, 2 warnings, ~7.5 s). Deterministic; judged by
             exit code (P24), no flake observed.

---

## FINDINGS

F1 · P25 · web/app.py:84 + web/static/app.js (0 reads) + tests/web/test_app.py:130 · HIGH (high confidence)
  `startup_warnings` is exposed on `/healthz` (app.py:84) and populated on
  `AppContext` (deps.py:62), and the GUI renders it (gui.py:1978) — but
  `web/static/` contains zero references to `startup_warnings`/`healthz`/`warnings`,
  so the web front end never fetches or renders it. A web-only user runs degraded
  (Unpaywall off, polite-pool bare `biorx/1.0` UA) with no visible signal. The test
  `test_h_healthz_surfaces_startup_warnings_when_no_contact_email`
  (tests/web/test_app.py:130-144) asserts only `"startup_warnings" in body` and that
  one warning mentions `BIORX_CONTACT_EMAIL` — it proves the route emits the key,
  not that any user sees it, which is the P25 gap (a test written one layer below the
  surface). Honest deferral recorded in TODO.md:8-12, but the test's docstring
  ("so a web caller can surface…") reads as done while the surface does not exist.
  Fix: add an app-load check in `web/static/app.js` that reads `/healthz` and renders
  `startup_warnings`, with a front-end test asserting the value reaches the DOM (not
  just the JSON).

F2 · P5 · src/paper_meta.py:31-42, 251-258, 261-270 · MEDIUM (high confidence)
  The "no contact email" warning is emitted only in `SourceOrchestrator._register_adapters`
  (orchestrator.py:131-143). The abstract-recovery paths in paper_meta.py —
  `openalex_user_agent` (:31-42), `_europepmc` (:251-258), `_crossref_abstract`
  (:261-270) — call `load_sources_config()` directly and send a bare `biorx/1.0` UA
  with no warning. So a config-only caller using recovery (CLI/library) gets the
  degraded mode silently, an incomplete P5 class fix. The `try/except Exception → cfg = {}`
  wrappers at :38-41, :254-257, :267-270 are dead code: `load_sources_config`
  (config.py:50-71) has its own internal try/except and never raises. Fix: emit the
  no-email warning once from the shared config layer (`get_contact_email` or
  `load_sources_config`) so every consumer surfaces it, and drop the dead wrappers.

F3 · P2 · gui.py:2000-2003 · MEDIUM (high confidence it occurs; low severity, GUI retiring)
  `_show_startup_warnings` loops `for msg in warnings: self.statusBar().showMessage(f"⚠ {msg}", 0)`.
  With both warnings firing (Unpaywall-off and polite-pool-no-email when email is
  unset), each `showMessage` overwrites the previous, so only the last stays visible;
  the first is dropped from view (still logged). Fix: accumulate the messages
  (join with `\n`, or use a QMessageBox) so every warning reaches the user.

---

## Verdict note

3 findings — 1 high (F1), 2 medium (F2, F3). Not clean. The batch's contact-email
wiring is otherwise complete class-wide (all seven search adapters, Crossref,
Unpaywall, BioRxivAPI, SearchAgent.api, and paper_meta's recovery paths are wired),
and the test/QA hardening in this range is well-constructed and P27/P28/P34-compliant.
The reject is on the transparency moat, not correctness: the degraded mode is not
surfaced on the primary (web) front end (F1, the same gap that has persisted across
prior gate passes, now masked by a route-level test), the no-email warning is not
emitted on the recovery paths (F2), and the GUI status bar drops all but the last
warning (F3). Fix F1–F3 and re-gate the fix commit as its own range before this
merges; F1 is the blocker — the others are in-loop or backlog candidates.
