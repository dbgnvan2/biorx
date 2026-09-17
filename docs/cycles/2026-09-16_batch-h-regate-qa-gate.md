# Learning-QA gate — batch-h (re-gate at HEAD 1b0a438), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding falsifies the chunk's core claim class-wide.
The batch-h contact-email hygiene (ad2818e and its fix chain, up through the
F1-F3 fix a75924f "complete contact-email wiring for all polite-pool consumers")
wired the polite-pool mailto into Crossref (orchestrator.py:112-114 and
paper_meta.py:273), Unpaywall (email required), arXiv (arxiv.py:118), and OpenAlex
(paper_meta.py:31 openalex_user_agent), but three live sibling search adapters —
Europe PMC (europepmc.py:100), PsyArXiv (psyarxiv.py:33), and SocArXiv
(socarxiv.py:33) — still send the hardcoded `ResearchTool/1.0` with no mailto, and
an inconsistent product string. Europe PMC is the highest-traffic search source in
the app and is registered unconditionally when enabled (orchestrator.py:80-82).
One medium finding compounds it: the "no contact email" degraded-mode warning covers
Crossref + arXiv only (orchestrator.py:134-145), while OpenAlex (paper_meta.py:31)
still degrades silently to a bare `biorx/1.0` with no user-visible signal. The
test/QA hardening in this range remains well-constructed and P27/P28/P34/P35-
compliant — the suite is green and deterministic — but the feature is incomplete
as shipped.

---

## Report

RANGE:       `git diff origin/main...HEAD` (three dots; merge-base
             5ffad2e16a34b88ab77662c467e8d1c518b58286, HEAD 1b0a438), materialized
             to /tmp/sweep.diff (2,308 lines, 24 files, +1868/-57). Selected by
             step-1 rule #1 (caller-supplied range, used exactly).

COMMITS:     20 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, CI); 14 fix/chore
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, 10220a4, a75924f, 1b0a438:
             test-qa loop findings P28/P21/P27/P35/P19/P31/P5, chdp F1-F3,
             ArxivAdapter wiring, contact-email F1-F3 fix, BASELINE advances).

APPLICABLE:  P1, P2, P3, P4, P5, P6, P8, P19, P21, P22, P27, P28, P29, P31, P34, P35

CHECKED:     P1 (transient-as-permanent: no terminal negative written on empty
             email — Unpaywall disabled-with-warning, not "no OA"; 429 paths
             unchanged), P2 (silent drop: Unpaywall skip is loud; enrich loop
             unchanged), P3 (narrow scope: all polite-pool consumers + sibling
             adapters enumerated by grep), P4 (hardcoded constants: contact email
             extracted to env/config; product string only partially unified — see
             F1), P5 (sibling robustness: grepped every external-call UA site —
             F1 and F2), P6 (status-vs-artifact: startup warnings derive from a
             real config read, no fake state), P8 (dirty state: conftest redirects
             to temp paths), P19 (source-text assertions: email scanner AST-parse,
             guard-the-guard tests), P21 (built-not-wired: SearchCache still zero
             callers, acknowledged not claimed), P22 (legacy `crossref_user_agent`
             key still unread — prior-gate F4, explicitly deferred, not re-opened),
             P27 (cannot-fail tests: drift `_git` helper skip→fail; fix-commit test
             would fail if unwired), P28 (default-arg production reach:
             BIORX_DB_PATH/BIORX_CACHE_PATH redirect + real-artifact fingerprint),
             P29 (floor assertions: none — all exact counts/strings), P31
             (absence-over-narrowed-population: fingerprint returns None on partial
             walk → guard fails), P34 (entry-point side effects: GUI test mocks
             Database/SummarizationAgent/load_filters, offscreen Qt), P35
             (unproven baseline: drift test fails not skips on unreachable
             BASELINE). P7/P9/P10/P11-P18/P20/P23/P24/P26/P30/P32/P33 not
             applicable: no scoring/ranking, LLM, background-worker, or
             graph/traversal code in range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked /
             working-tree files NOT in the range: .test-qa-report.md, Social
             Baseline Theory.xlsx, untracked docs/cycles/*-qa-gate.md,
             docs/epigenetic-inheritance-review/, and the staged deletion of
             ARXIV_ADAPTER_TASK.md. Docs-only changes reviewed only for
             personal-address leakage, not content. Scope-limit families NOT
             assessed: logic/algorithmic correctness, concurrency/races,
             authn/authz, injection/security, performance, dependency/supply-chain,
             API contract compatibility, architecture, and test quality beyond
             P27/P28/P29/P32. This repo has NO `./LEARNINGS.md` (verified) — the
             sweep ran against the generic P1-P35 catalogue in
             ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (518 passed, 19 skipped, ~7.6 s). Deterministic; judged by exit code,
             no flake observed.

---

## FINDINGS

F1 · P5 · src/sources/europepmc.py:100 (+ psyarxiv.py:33, socarxiv.py:33) · HIGH (high confidence)
  The batch-h "unify product string / wire contact email" fix reached Crossref,
  Unpaywall, arXiv, and OpenAlex but left three sibling search adapters hardcoded
  to `"ResearchTool/1.0"` with no mailto: Europe PMC (the highest-traffic source,
  registered at orchestrator.py:80-82), PsyArXiv (orchestrator.py:90-92), and
  SocArXiv (orchestrator.py:95-97). F3's own fix note said "unify on biorx/1.0";
  that unification landed on only 2 of 5+ sites. A real user running a default
  search hits Europe PMC with no contact email and an inconsistent product string.
  Fix: thread `polite_user_agent(self.config)` (or a mailto-carrying UA) into
  EuropePmcAdapter/PsyArxivAdapter/SocArxivAdapter construction at the orchestrator
  registration sites, and add a test asserting the Europe PMC UA carries the mailto
  when an email is set (mirroring test_h_user_agent_has_mailto_only_when_an_address_is_set).

F2 · P5 · src/sources/orchestrator.py:134-145 (+ src/paper_meta.py:31) · MEDIUM (high confidence)
  The "no contact email" degraded-mode warning gates on `crossref or arxiv` only,
  omitting OpenAlex — the fourth named polite-pool consumer. `openalex_user_agent`
  (paper_meta.py:31) silently sends a bare `biorx/1.0` with no mailto when no email
  is set, with no user-visible signal (residual of prior-gate F2, addressed 3-of-4).
  Fix: add OpenAlex to the startup warning, or state explicitly in config/comment
  why the abstract-recovery OpenAlex path is deliberately outside the startup
  warning's scope.

---

## Verdict note

2 findings — 1 high (F1), 1 medium (F2). Not clean. The F1-F3 fix (a75924f)
extended contact-email wiring to Crossref/Unpaywall/arXiv/OpenAlex but did not
finish the class: Europe PMC, PsyArXiv, and SocArXiv — all live registered search
adapters — still send `ResearchTool/1.0` with no mailto (F1), and OpenAlex still
degrades silently with no warning (F2). The test/QA hardening itself remains
P27/P28/P34/P35-compliant. Fix F1-F2 and re-gate the fix commit as its own range
before this merges.
