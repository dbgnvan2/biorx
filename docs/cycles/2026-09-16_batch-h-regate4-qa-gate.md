# Learning-QA gate — batch-h (re-gate at HEAD 0d641c7), 2026-09-16

**Verdict: REJECTED**

Binary: REJECTED. The a55b83e fix claims it "closes the P5 class" — all seven
enabled-source adapters plus the OpenAlex path now send a polite User-Agent
carrying the config contact email. The claim is false. Two live sibling HTTP
consumers were left unwired: `SearchAgent` (gui.py + CLI) still builds
`BioRxivAPI()` bare at agents/search_agent.py:38, and `_europepmc()` still
returns `EuropePmcAdapter()` bare at src/paper_meta.py:253 — while its literal
sibling `_crossref_abstract` was fixed in the same change. Both send
`biorx/1.0` with no contact email on real user paths (GUI abstract recovery,
web summarize, `biorx search`). Two medium findings compound it: the no-email
`startup_warnings` are computed and returned on `/healthz` but no web front-end
reads them (P25), and the "no personal address in shipped markdown" scan is
root-`*.md` only, silently excluding all of `docs/` (P3, latent). The
test/QA hardening in this range is well-constructed and the suite is green and
deterministic, but the feature is incomplete as shipped.

---

## Report

RANGE:       `git diff origin/main..HEAD` (26 commits, HEAD = 0d641c7). Selected
             by step-1 rule #1 (caller-supplied range, used exactly). Two-dot and
             three-dot are identical here because origin/main is an ancestor of
             HEAD. Materialized to /tmp/sweep.diff (2,537 lines, 29 files,
             +1970/-71); reproducible via `git diff origin/main..HEAD`.

COMMITS:     26 — 5 docs (e6cd8ec, ad6863a, c23b18a, f31672d, 6da4547: spec
             revisions, setup guides, plans PD1–PD5); 1 feat (ad2818e batch-h:
             contact email from env, no personal address in source, CI); 15 fix
             (531b24f, 039f16b, 5c15532, 1b9021c, 6af0902, 4eb05d0, 4d92d2b,
             9fe188d, e1fea58, a0e5edc, fa4665c, a75924f, febd2fd, 128cef6,
             a55b83e); 5 chore BASELINE advances (10220a4, 1b0a438, 23e26bc,
             065bede, 0d641c7). Newest two (a55b83e fix + 0d641c7 BASELINE)
             are the least-reviewed surface and were swept as their own range.

APPLICABLE:  P2, P3, P4, P5, P19 (corollary), P21, P24, P25, P26, P27, P28,
             P31, P35.

CHECKED:     P5 (sibling hardening, class-wide: grepped every HTTP consumer and
             every `BioRxivAPI(`/`BiorxivMedrxivAdapter(`/`*Adapter(` construction
             across src/, agents/, gui.py, web/ — **two siblings left unwired**:
             F1, F2), P3 (narrow scope: `_root_md_files` scans root `*.md` only —
             F4), P25 (front-end wiring: startup_warnings reach `/healthz` JSON
             but no web/static JS reads them — F3), P4 (hardcoded/placeholder
             address removal; `biorx/1.0` promoted to a named default), P2
             (silent drop: Unpaywall no-email skips registration with a logged +
             listed warning, not silent), P19 corollary (address scanner
             AST-parses, not raw-text substring), P21 (built-not-wired), P24
             (suite judged by exit code), P26 (all five fix commits swept as
             ordinary code), P27 (new tests assert real boundary values, not
             constants/comments), P28 (conftest fingerprint guard +
             BIORX_DB_PATH/BIORX_CACHE_PATH redirect), P31/P35 (fingerprint
             guard returns None on partial snapshot → fails, not clean; drift
             test fails rather than skips). P6/P7/P8/P9/P10/P11–P18/P20/P22/P23/
             P29/P30/P32/P33/P34 not applicable: no scoring/ranking, LLM,
             background-worker, graph/traversal, or refactor-dropped-side-effect
             code in range.

NOT COVERED: no caller-excluded paths (range was the whole branch). Untracked /
             working-tree files NOT in the range: .test-qa-report.md, Social
             Baseline Theory.xlsx, the untracked docs/cycles/2026-09-16_batch-h-*
             -qa-gate.md files, docs/epigenetic-inheritance-review/, and the
             staged deletion of ARXIV_ADAPTER_TASK.md. Docs-only changes reviewed
             only for personal-address leakage, not content. Live HTTP not
             exercised (adapter tests are mock/recorded-fixture unit tests).
             Scope-limit families NOT assessed: logic/algorithmic correctness,
             concurrency/races, authn/authz, injection/security, performance,
             dependency/supply-chain, API contract compatibility, architecture,
             and test quality beyond P27/P28/P29/P32. This repo has NO
             ./LEARNINGS.md (verified) — the sweep ran against the generic
             P1–P35 catalogue in ~/.claude/standards/learnings.md only.

TEST GATE:   `/opt/homebrew/bin/pytest tests/ -q` → exit code 0
             (524 passed, 19 skipped, ~7.3 s). Deterministic; judged by exit
             code (P24), no flake observed. Two benign deprecation warnings
             (httpx/Starlette, HTTP_413 constant), unrelated to this range.

---

## FINDINGS

F1 · P5 · agents/search_agent.py:38 · HIGH (high confidence)
  `SearchAgent.__init__` builds `self.api = BioRxivAPI()` with no `user_agent`,
  so every bioRxiv search through this agent sends the default `biorx/1.0` with
  no contact email. The agent is live: imported by gui.py:25 and invoked at
  agents/search_agent.py:259 (CLI `python agents/search_agent.py --all`), per
  README/QUICKSTART/run.sh. The a55b83e fix wired only the orchestrator →
  BiorxivMedrxivAdapter path; this sibling consumer of BioRxivAPI is unwired, so
  the "closes the P5 class" claim is false. (PD1 in the make-it-great plan does
  propose deleting this agent, but that plan is not yet executed — it is still
  on the run path today.) Fix: load config and pass
  `user_agent=polite_user_agent(cfg)` here, or complete PD1 (route `biorx search`
  through the orchestrator) so the bare agent no longer exists.

F2 · P5 · src/paper_meta.py:253 · HIGH (high confidence)
  `_europepmc()` returns `EuropePmcAdapter()` with no `sources_config`, so the
  Europe PMC abstract-recovery and PMC full-text requests send bare `biorx/1.0`
  for any user who sets `contact_email` in `sources_config.yaml` (the documented
  config path). Its literal sibling `_crossref_abstract` (same file, line 273)
  was fixed in this change to `load_sources_config()` +
  `get_crossref_user_agent(cfg)`; `_europepmc()` was left bare — the exact P5
  half-fix shape. `recover_abstract` (which calls `_europepmc()` as step 1) is
  reachable from gui.py:256 and web/routes_summaries.py:136, so real users hit
  it. Env-only users are unaffected, which is why the env-keyed tests miss it.
  Fix: mirror `_crossref_abstract` — `cfg = load_sources_config()` (guarded) →
  `EuropePmcAdapter(sources_config=cfg)`, and add a test asserting the Europe PMC
  recovery path's UA carries the mailto from the config file.

F3 · P25 · web/app.py:64-85 + web/static/app.js · MED (medium confidence)
  `startup_warnings` are computed (deps.py:62), stored, and returned on `/healthz`
  (app.py:84), and a test asserts the JSON contains them — but no front-end code
  reads `/healthz` or `startup_warnings` (grep of web/static for healthz /
  startup_warnings / warning / BIORX_CONTACT / contact_email returns zero). Web
  users therefore silently run in the degraded default state (contact_email
  defaults to "" → Unpaywall off, bare polite-pool UAs) with nothing surfaced;
  the GUI path shows it in the status bar, but the web path stops at the healthz
  JSON. Fix: surface startup_warnings in the web UI (read /healthz at boot, render
  a banner), or confirm /healthz is launcher-only and record that decision.

F4 · P3 · tests/test_h_environment.py:38-44 · MED (latent)
  `_root_md_files` scans `ROOT.glob("*.md")` only. The docstring says the
  exclusion is `docs/cycles/`, but "don't recurse" also silently excludes the
  current shipped docs under `docs/` (spec_make_it_great.md, guides, the two
  implementation plans). Today no personal address actually exists in non-cycles
  docs/, so nothing is currently missed — latent. Fix: scan `docs/` minus an
  explicit `docs/cycles/` exclusion (or assert the specific shipped files), so a
  future address in a shipped guide is caught.

---

## Verdict note

4 findings — 2 high (F1, F2), 2 medium (F3, F4). Not clean. The a55b83e fix is
correct for the one path it touched (orchestrator → BiorxivMedrxivAdapter now
sends `polite_user_agent(config)`, warning and test updated), but it repeated the
P5 half-fix it was meant to end: two live sibling consumers — SearchAgent's
`BioRxivAPI()` and abstract-recovery's `_europepmc()` — still send a
contact-email-less User-Agent, and the no-email warning never reaches web users.
The prior gate's F1 (bioRxiv/medRxiv) is closed for the orchestrator path; the
class is not. Fix F1 and F2 (both are one-line config-threading changes + a test
each) and re-gate the fix commits as their own range before this merges. F3 and
F4 are medium/latent and can go to the backlog, but should not be silently
dropped.
