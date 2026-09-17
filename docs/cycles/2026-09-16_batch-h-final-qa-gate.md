# Learning-QA gate — batch-h (full branch), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding falsifies the chunk's core claim class-wide.
The batch-h contact-email hygiene (ad2818e and its fix chain) wired the polite-pool
mailto into the orchestrator's Crossref registration (orchestrator.py:112-114) and
OpenAlex (openalex_user_agent, paper_meta.py:31), but a live sibling Crossref call
site — `_crossref_abstract`, step 3 of abstract recovery, reachable from both
gui.py and the web app — still constructs `CrossrefAdapter()` with no `user_agent`,
sending the hardcoded `ResearchTool/1.0` with no mailto. Two medium findings compound
it: the "no contact email" condition is surfaced loudly only for Unpaywall (Crossref/
OpenAlex/arXiv degrade silently), and the placeholder defaults (`research@example.com`,
`ResearchTool/1.0`) still sit in unpaywall.py/crossref.py, contradicting the new
"never a placeholder address" invariant. The test/QA hardening in this range is itself
well-constructed and P27/P28/P34-compliant — the suite is green and deterministic —
but the feature is incomplete as shipped.

---

## Report

RANGE:       `git diff origin/main...HEAD` (three dots, merge base = origin/main).
             Selected by step-1 rule #1 (caller-supplied range, used exactly).
             Materialized to /tmp/sweep.diff (2,184 lines, 22 files, +1789/-54);
             reproducible via the same command.

COMMITS:     18 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, CI); 12 fix/chore
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, 10220a4: test-qa loop findings
             P28/P21/P27/P35/P19/P31/P5, then chdp F1-F3, ArxivAdapter wiring,
             BASELINE advance).

APPLICABLE:  P1, P2, P3, P4, P5, P6, P8, P19, P21, P22, P27, P28, P29, P31, P34, P35

CHECKED:     P1 (transient-as-permanent: Unpaywall 429 still raises, not a negative),
             P2 (silent drop: orchestrator Unpaywall warns; enrich loop logs per-DOI),
             P3 (narrow scope: source enumeration, scan dirs), P4 (hardcoded
             constants: contact email extraction, placeholder defaults), P5 (sibling
             robustness: all four polite-pool consumers + second Crossref call site),
             P6 (status vs artifact: warnings reflect real missing email), P8 (dirty
             state: conftest redirects), P19 (source-text assertions: AST-parse not
             substring; guard-the-guard tests), P21 (built-not-wired: SearchCache zero
             callers — acknowledged, not claimed integrated), P22 (contract drift:
             crossref_user_agent key), P27 (fail-able tests: git-fail vs skip),
             P28 (default-arg production reach: BIORX_DB_PATH/BIORX_CACHE_PATH
             redirect + fingerprint), P29 (floor assertions: none found — all exact),
             P31 (absence over narrowed population: email scan scope), P34 (entry-point
             side effects: GUI test mocks Database/SummarizationAgent/filters),
             P35 (unproven baseline: drift-test git failure now fails not skips).
             P7/P9/P10/P11-P18/P20/P23/P24/P26/P30/P32/P33 not applicable: no
             scoring/ranking, LLM, background-worker, or graph/traversal code in range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked files
             not reviewed (`git diff` only): .test-qa-report.md, Social Baseline
             Theory.xlsx, docs/cycles/*-qa-gate.md (untracked), docs/epigenetic-
             inheritance-review/. Docs-only changes reviewed only for personal-address
             leakage, not content. Scope-limit families NOT assessed: logic/
             algorithmic correctness, concurrency/races, authn/authz, injection/
             security, performance, dependency/supply-chain, API contract
             compatibility, architecture, and test quality beyond P27/P28/P29/P32.
             This repo has NO `./LEARNINGS.md` (verified) — the sweep ran against the
             generic P1–P35 catalogue in ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (516 passed, 19 skipped, ~7.6 s). Deterministic; judged by exit code
             (P24), no flake observed.

---

## FINDINGS

F1 · P5 · src/paper_meta.py:268 · HIGH (high confidence)
  `_crossref_abstract` (step 3 of `recover_abstract`) constructs `CrossrefAdapter()`
  with no `user_agent`, so the live abstract-recovery path falls back to the hardcoded
  `ResearchTool/1.0` (crossref.py:46) with no mailto. Reachable from gui.py and
  web/routes_summaries.py, so a real user hits it. The batch-h fix wired Crossref in
  the orchestrator (orchestrator.py:112-114 via `get_crossref_user_agent`) and OpenAlex
  (`openalex_user_agent`, this same file) but missed this sibling call site. Fix: pass
  `user_agent=get_crossref_user_agent(load_sources_config())` at this call site (or
  thread the loaded config through `recover_abstract`), and add a test asserting the
  abstract-recovery Crossref path's UA carries the mailto when an email is set.

F2 · P5 · src/sources/orchestrator.py:116-129 · MEDIUM (high confidence)
  The "no contact email" condition is surfaced loudly only for Unpaywall (warning +
  disabled). Crossref (112-114), OpenAlex, and arXiv silently proceed with a bare
  `biorx/1.0` UA (no mailto) when email is empty — three polite-pool consumers degrade
  with no user-visible signal, against the project's own transparency moat. Fix: emit
  a startup warning whenever `get_contact_email(config)` is empty while any
  polite-pool consumer is enabled, so the degraded mode is user-visible.

F3 · P4 · src/sources/unpaywall.py:26 + src/sources/crossref.py:46 · MEDIUM (high confidence)
  Placeholder defaults remain — `email="research@example.com"` and
  `user_agent="ResearchTool/1.0"` — directly contradicting config.py's new invariant
  "never falls back to a placeholder address", and the product string is now
  inconsistent (`ResearchTool/1.0` vs `biorx/1.0`). Currently unreachable only because
  the orchestrator guards construction with `if email:`; any future direct construction
  sends a fake contact. Fix: make `email`/`user_agent` required (or default to the
  config-resolved value) and unify the product string on `biorx/1.0`.

F4 · P22 · src/sources/config.py:127 + tests/test_orchestrator.py:42 · LOW (medium confidence)
  `get_crossref_user_agent` no longer reads the legacy `crossref_user_agent` config key
  (now dead — `get_contact_email` reads only `contact_email`/`unpaywall_email`,
  config.py:106), while the sibling legacy key `unpaywall_email` is still read — an
  asymmetric migration; an existing `sources_config.yaml` that set `crossref_user_agent`
  is silently ignored. Fix: read the legacy `crossref_user_agent` key as a fallback in
  `get_contact_email`, or delete the dead key from tests/test_orchestrator.py and note
  the breaking change.

---

## Verdict note

4 findings — 1 high (F1), 2 medium (F2, F3), 1 low (F4). Not clean. The batch-h
contact-email hygiene was applied to the orchestrator's registration path and OpenAlex
but not class-wide: one live sibling Crossref call site (F1) and three polite-pool
consumers (F2) remain on non-compliant defaults with no user-visible signal, and the
placeholder defaults (F3) contradict the chunk's own invariant. The test/QA hardening
itself is well-constructed and compliant with P27/P28/P34. Fix F1–F3 and re-gate the
fix commit as its own range before this merges.
