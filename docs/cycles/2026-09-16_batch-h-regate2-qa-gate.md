# Learning-QA gate — batch-h (re-gate at HEAD 23e26bc), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. One high finding falsifies the chunk's core claim class-wide.
The batch-h contact-email hygiene (ad2818e and its fix chain, through the F1-F3
fix a75924f and the EuropePMC/PsyArXiv/SocArXiv fix febd2fd) now wires the
config-file `contact_email` into Crossref, Unpaywall, arXiv, OpenAlex, Europe PMC,
PsyArXiv, and SocArXiv — but not into **PubMed**, which subclasses EuropePmcAdapter
and is constructed bare at orchestrator.py:87. PubMed is `enabled: true` and
`default_selected: true`, so a default search hits the Europe PMC API through it
with a bare `biorx/1.0` (no mailto) whenever the user set `contact_email` in
sources_config.yaml rather than the env var. Two further findings compound it: the
"no contact email" startup warning and the shipped YAML comment enumerate an
incomplete consumer set (both omit PubMed; the YAML says a stale "all four
sources"), and the bioRxiv/medRxiv wrapper still sends requests' default
`python-requests/x.y` with no identifying UA. The test/QA hardening in this range
remains well-constructed and P27/P28/P34/P35-compliant — the suite is green and
deterministic — but the feature is incomplete as shipped.

---

## Report

RANGE:       `git diff origin/main...HEAD` (three dots; merge-base = origin/main
             = 5ffad2e16a34b88ab77662c467e8d1c518b58286, HEAD = 23e26bc), 22
             commits, 27 files, +1935/-66. Materialized to /tmp/sweep.diff
             (2463 lines). Selected by step-1 rule #1 (caller-supplied range,
             used exactly). Note: caller wrote `origin/main..HEAD`; two-dot and
             three-dot are identical here because origin/main is the merge base.

COMMITS:     22 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, plans, setup guides); 1 feat (ad2818e batch-h: contact
             email from env, no personal address in source, CI); 13 fix/chore
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, a75924f, febd2fd: test-qa loop
             findings P28/P21/P27/P35/P19/P31/P5, chdp F1-F3, ArxivAdapter
             wiring, contact-email F1-F3 fix, EuropePMC/PsyArXiv/SocArXiv
             wiring); 3 chore BASELINE advances (10220a4, 1b0a438, 23e26bc).

APPLICABLE:  P1, P2, P3, P4, P5, P6, P8, P10, P19, P21, P25, P27, P28, P31,
             P34, P35.

CHECKED:     P1 (transient-as-permanent: 429 paths unchanged, no terminal
             negative written on empty email), P2 (silent drop: Unpaywall skip
             is loud; enrich loop unchanged), P3 (narrow scope: enumerated every
             source adapter / external HTTP site — Crossref, Unpaywall, arXiv,
             OpenAlex, Europe PMC, PubMed, PsyArXiv, SocArXiv, bioRxiv/medRxiv;
             F2 is the residual), P4 (hardcoded constants: contact email
             extracted to env/config; "all four sources" count is stale — F2),
             P5 (sibling robustness: grepped every UA/construction site; F1 and
             F3), P6 (status-vs-artifact: warnings derive from a real config
             read), P8 (dirty state: conftest redirects to temp paths), P10
             (fix→test map: PubMed has NO UA test — the omission mirrors the
             bug), P19 (source-text assertions: email scanner AST-parses; no
             substring drift), P21 (built-not-wired: SearchCache zero callers,
             acknowledged not claimed), P25 (front-end wiring: each adapter's
             config path tested at the adapter, not just the loader), P27
             (cannot-fail tests: exact `==` UA assertions would fail on
             regression), P28 (default-arg production reach: BIORX_DB_PATH /
             BIORX_CACHE_PATH redirect + real-artifact fingerprint), P31
             (absence over narrowed population: fingerprint returns None on
             partial walk → guard fails), P34 (entry-point side effects: GUI
             test mocks Database/SummarizationAgent/load_filters, offscreen Qt),
             P35 (unproven baseline: drift test fails not skips). P7/P9/P11-P18/
             P20/P23/P24/P26/P30/P32/P33 not applicable: no scoring/ranking,
             LLM, background-worker, or graph/traversal code in range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked /
             working-tree files NOT in the range: .test-qa-report.md, Social
             Baseline Theory.xlsx, the four untracked docs/cycles/2026-09-16_
             batch-h-*-qa-gate.md files, docs/epigenetic-inheritance-review/,
             and the staged deletion of ARXIV_ADAPTER_TASK.md. Docs-only changes
             reviewed only for personal-address leakage, not content. Live HTTP
             not exercised (adapter tests are mock/recorded-fixture unit tests).
             PDF-download and publisher-scrape paths (pdf_handler.py, agents/
             monitor.py) are a different class (publisher downloads, not polite
             pools) and were not assessed against the polite-pool claim.
             Scope-limit families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API contract compatibility, architecture,
             and test quality beyond P27/P28/P29/P32. This repo has NO
             ./LEARNINGS.md (verified) — the sweep ran against the generic
             P1-P35 catalogue in ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (522 passed, 19 skipped, ~7.4 s). Deterministic; judged by exit
             code (P24), no flake observed. Two benign deprecation warnings
             (httpx/Starlette, HTTP_413 constant), unrelated to this range.

---

## FINDINGS

F1 · P5 · src/sources/orchestrator.py:87 · HIGH (high confidence)
  `PubMedAdapter()` is constructed bare, while its sibling `EuropePmcAdapter`
  (orchestrator.py:82) is constructed with `sources_config=self.config`.
  `PubMedAdapter` subclasses `EuropePmcAdapter`, whose `__init__` sets
  `self.sources_config = sources_config or {}` and builds the UA via
  `polite_user_agent(self.sources_config)`. With an empty config dict,
  `get_contact_email({})` reads only the env var, so a `contact_email` set in
  `sources_config.yaml` never reaches PubMed's User-Agent — only the
  `BIORX_CONTACT_EMAIL` env path works. PubMed is `enabled: true` and
  `default_selected: true` (sources_config.yaml:6-8), so a default search hits
  the Europe PMC API through this subclass with a bare `biorx/1.0` (no mailto).
  Fix: `PubMedAdapter(sources_config=self.config)`, and add a test mirroring
  `test_h_europepmc_carries_config_contact_address` (tests/test_h_environment.py:
  388) asserting PubMed's UA carries the config-file mailto.

F2 · P3 · src/sources/orchestrator.py:136-139 + sources_config.yaml:43-44 · MEDIUM (high confidence)
  The "no contact email" startup warning enumerates "Crossref, arXiv, Europe
  PMC, PsyArXiv, SocArXiv, OpenAlex" but omits PubMed — a default-enabled
  polite-pool consumer on the same Europe PMC API. The shipped YAML comment
  says "covers all four sources … All four sources also read this field
  directly" (sources_config.yaml:43-44), a stale hardcoded count that no longer
  matches the real consumer set (and duplicates the comment at lines 39-41).
  Fix: add PubMed to the warning string, and correct the YAML comment to the
  actual consumer list (or drop the count).

F3 · P5 · src/biorxiv_api.py:19-21 · LOW (medium confidence)
  `BioRxivAPI.__init__` builds a bare `requests.Session()` with no User-Agent,
  so bioRxiv/medRxiv requests (via gui.py:28, agents/search_agent.py:38, and
  the BiorxivMedrxivAdapter at biorxiv_medrxiv.py:31-32) send requests' default
  `python-requests/x.y` — unidentifiable, no product string. bioRxiv's API has
  no mailto requirement, so this is identifiability hygiene rather than a
  polite-pool defect, and may be a deliberate scoping decision (bioRxiv is not
  named in the polite-pool warning). Fix: set a `biorx/1.0` UA on the session,
  or record bioRxiv as deliberately out of scope in the warning comment.

---

## Verdict note

3 findings — 1 high (F1), 1 medium (F2), 1 low (F3). Not clean. The prior-gate
F1/F2 fix (febd2fd) completed the class for Europe PMC, PsyArXiv, and SocArXiv,
but the same P5 class has one more member: PubMed, a default-selected subclass of
EuropePmcAdapter constructed without config, so the config-file `contact_email`
path is still broken for it (F1). The startup warning and YAML comment also still
enumerate an incomplete consumer set (F2). The test/QA hardening itself remains
P27/P28/P34/P35-compliant. Fix F1-F2 and re-gate the fix commit as its own range
before this merges.
