# Learning-QA gate — batch-h (re-gate at HEAD 065bede), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding falsifies the chunk's completeness claim class-wide.
The contact-email / User-Agent hygiene (ad2818e and its fix chain, through the F1-F2
fix 128cef6) now wires the config-file `contact_email` into Crossref, Unpaywall,
arXiv, OpenAlex, Europe PMC, PubMed, PsyArXiv, and SocArXiv — but not into
**bioRxiv/medRxiv**. `BiorxivMedrxivAdapter` is `enabled: true` in `_DEFAULT_CONFIG`
(config.py:19) and the shipped `sources_config.yaml`, is registered and queried in the
"all sources" path (orchestrator.py:100-103, `_resolve_active_sources`), and still
sends requests' default `python-requests/x.y` with no product string and no contact
(biorxiv_api.py:21, bare `requests.Session()`). Commit a75924f claimed "complete
contact-email wiring for **all** polite-pool consumers" while leaving this one out —
the completeness claim is false, and the P5 meta-rule (grep the whole class) is
exactly what would have caught it. The prior gate (regate2) recorded this as F3 at
LOW severity; the cold pass re-reads it as HIGH because a default "all sources" search
hits it and no comment or doc marks it as a deliberate exclusion. One low finding
(healthz constructing the full orchestrator) is flagged but below the in-loop-fix
threshold. The test/QA hardening in this range remains P27/P28/P34/P35-compliant — the
suite is green and deterministic — but the feature is incomplete as shipped.

---

## Report

RANGE:       `origin/main..HEAD` (24 commits, HEAD = 065bede). Selected by step-1
             rule #1 (caller-supplied range, used exactly). Two-dot and three-dot
             are identical here because origin/main is the merge base. Materialized
             to /tmp/sweep.diff (2,479 lines, 27 files, +1949/-67); reproducible via
             `git diff origin/main...HEAD`.

COMMITS:     24 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact email
             from env, no personal address in source, CI); 14 fix (531b24f, 039f16b,
             5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b, 9fe188d, e1fea58, a0e5edc,
             fa4665c, a75924f, febd2fd, 128cef6: test-qa loop findings, chdp F1-F3,
             contact-email wiring, ArxivAdapter config, PubMedAdapter F1-F2 fix);
             4 chore BASELINE advances (10220a4, 1b0a438, 23e26bc, 065bede).

APPLICABLE:  P1, P2, P3, P4, P5, P6, P13, P19, P27, P28, P29, P34, P35.

CHECKED:     P5 (sibling hardening: grepped every HTTP consumer — crossref, unpaywall,
             arxiv, europepmc→pubmed, psyarxiv, socarxiv, openalex all wired;
             **biorxiv/medRxiv is not** — F1), P3 (narrow scope: consumer-list
             enumeration; warning string omits bioRxiv/medRxiv — F1), P1/P2 (Unpaywall
             no-email: skips registration with logged+listed warning, correct — not
             silent, not a fake address), P4 (hardcoded constants: get_contact_email /
             polite_user_agent env→config→empty precedence correct; no personal
             address in source), P6 (derived state: warnings derive from a real config
             read), P13 (guard scope: healthz constructs orchestrator — F2), P19
             corollary (source-text assertions: email scanner AST-parses, no substring
             drift), P27 (cannot-fail tests: all new tests have real asserts),
             P28 (default-arg production reach: BIORX_DB_PATH/BIORX_CACHE_PATH
             redirect + fingerprint), P29 (floor assertions: none found — all exact or
             membership), P34 (entry-point side effects: GUI test mocks
             Database/SummarizationAgent/load_filters), P35 (unproven baseline: drift
             test fails not skips). Fix commit 128cef6 reviewed as ordinary code:
             PubMedAdapter(sources_config=self.config) inherits EuropePmcAdapter's
             polite_user_agent header correctly; new test is a real behavioural
             assertion. P7/P8/P9/P10/P11/P12/P14-P18/P20-P26/P30-P33 not applicable:
             no scoring/ranking, LLM, background-worker, graph/traversal, or
             refactor-dropped-side-effect code in range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked /
             working-tree files NOT in the range: .test-qa-report.md, Social Baseline
             Theory.xlsx, the five untracked docs/cycles/2026-09-16_batch-h-*-qa-gate.md
             files, docs/epigenetic-inheritance-review/, and the staged deletion of
             ARXIV_ADAPTER_TASK.md. Docs-only changes reviewed only for personal-address
             leakage, not content. Live HTTP not exercised (adapter tests are
             mock/recorded-fixture unit tests). Scope-limit families NOT assessed:
             logic/algorithmic correctness, concurrency/races, authn/authz,
             injection/security, performance, dependency/supply-chain, API contract
             compatibility, architecture, and test quality beyond P27/P28/P29/P32.
             This repo has NO ./LEARNINGS.md (verified) — the sweep ran against the
             generic P1-P35 catalogue in ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (523 passed, 19 skipped, ~7.5 s). Deterministic; judged by exit code
             (P24), no flake observed. Two benign deprecation warnings
             (httpx/Starlette, HTTP_413 constant), unrelated to this range.

---

## FINDINGS

F1 · P5/P3 · src/biorxiv_api.py:21 + src/sources/biorxiv_medrxiv.py:30-32 + src/sources/orchestrator.py:102,136-141 · HIGH (high confidence)
  bioRxiv/medRxiv still sends requests' default `python-requests/x.y` with no product
  string and no contact. `BioRxivAPI.__init__` builds a bare `requests.Session()`
  (biorxiv_api.py:21); `BiorxivMedrxivAdapter.__init__` takes only `timeout` and does
  not thread `sources_config` into the API (biorxiv_medrxiv.py:30-32); and the
  orchestrator constructs it bare at orchestrator.py:102. It is `enabled: true` in
  `_DEFAULT_CONFIG` (config.py:19) and the shipped yaml, registered in
  `_register_adapters`, and returned by the "all sources" path — so a default search
  hits api.biorxiv.org unidentifiably. The orchestrator's no-email warning string
  (orchestrator.py:136-141) omits it from the consumer list, and the config comment
  at config.py:26-28 still enumerates only "(Crossref, OpenAlex, arXiv)". Commit
  a75924f claimed "all polite-pool consumers"; the class-wide fix skipped this member
  and no comment marks it excluded. Fix: thread `sources_config` into
  `BiorxivMedrxivAdapter` → `BioRxivAPI`, set `session.headers["User-Agent"] =
  polite_user_agent(sources_config)`, add "bioRxiv/medRxiv" to the warning string and
  correct the config comment, and add a test mirroring
  `test_h_pubmed_carries_config_contact_address` asserting the bioRxiv/medRxiv UA
  carries the mailto.

F2 · P13/P1 · web/app.py:75 · LOW (medium confidence)
  `/healthz` calls `c.get_orchestrator()`, so the liveness endpoint constructs the full
  orchestrator (imports and instantiates every enabled adapter). An unrelated adapter
  import failure turns the health probe into a 500, and this repo's own launcher plan
  (GL.1) uses healthz as the "is an instance already running" probe. Below the
  in-loop-fix threshold (bound-the-loop rule); flagged for the backlog. Fix (deferred):
  keep the liveness path free of orchestrator construction — wrap in try/except and
  return `ok:True` with `startup_warnings:[]` on failure, or surface warnings without
  building adapters.

---

## Verdict note

2 findings — 1 high (F1), 1 low (F2). Not clean. The prior gate's F1/F2 fix (128cef6)
is correct and the PubMed class member is now closed, but the same P5 class has one
member left: bioRxiv/medRxiv, enabled-by-default and reachable on the "all sources"
path, still on requests' default UA with no contact and omitted from the warning and
the config comment. The cold pass escalates it from LOW (prior gate's F3) to HIGH
because a default search actually reaches it and the completeness claim in a75924f is
false. Fix F1 and re-gate the fix commit as its own range before this merges. F2 is
below medium and goes to the backlog.
