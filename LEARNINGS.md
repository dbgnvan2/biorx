# BioRx — Failure Pattern Playbook

Purpose: prevent this project from repeating its own mistakes. This is a
reviewer's checklist, not documentation. Keep it lean and machine-scannable.
Patterns are at the class level, not the incident level. Target 5–12 patterns.

---

## Failure patterns

### P1 — Transient failure recorded as a permanent negative
A rate-limit / timeout / missing token is written as a terminal status.
Ask: *is this "no" actually "not right now"? Could a retry succeed?*

### P2 — Silent drop on failure
A call returns None/[]/\{\} on failure and the caller skips it; failed is
indistinguishable from genuinely empty; nothing logged.
Ask: *if this fails mid-batch, would anyone ever know?*

### P3 — Narrow-scope assumption that silently excludes
Only one source/tab/field is consulted; the rest is missed silently.
Ask: *what does looking in only one place exclude?*

### P4 — Hardcoded constant encoding a topic/time/business assumption
A literal date, year, magic threshold, or domain word baked into logic.
Ask: *should this be configuration?*

### P5 — Inconsistent robustness across sibling calls
One external call is hardened (retry/backoff/auth) but siblings aren't.
Ask: *are ALL calls of this kind hardened, or just the one that broke?*

### P6 — Trusting a derived/status field without verifying the artifact
A flag is believed without checking the file/row it claims exists.
Ask: *does this status reflect something real that exists now?*

### P7 — Gameable or over-weighted scoring proxy
A signal that is a proxy at compute time dominates and rewards the wrong thing.
Ask: *what input scores high for the wrong reason?*

### P8 — SSRF via stored URL proxied server-side
A server-side proxy fetches a URL stored in the database without validating
the scheme or host. Internal network addresses, `file://`, or `localhost`
URLs stored by any ingestion path become a server-side request forgery vector.
Ask: *does the proxy enforce `https://` and a public-host allowlist?*

### P9 — XSS sink in dynamically rendered content
External-sourced text (abstracts, titles, summaries from third-party APIs) is
inserted via `innerHTML` rather than `textContent`/`createTextNode`.
Ask: *does every DOM insertion of server-supplied text use a safe API?*

### P10 — A guard that lives in one front end
A precondition ("don't run an empty filter") is checked in the desktop GUI but
not in the shared code, so the web app, built by wrapping the engine, runs
without it. Catalogue: generic P25 (reverse case).
Ask: *is this guard in code every entry point goes through, with a test per entry point?*

### P11 — A stage whose output nothing reads
A stage runs, costs, and reports progress, but the consumer copied the data
before it ran. Catalogue: generic P36.
Ask: *if I delete this stage, which field in what the user receives changes — and which test fails?*

### P12 — One progress channel, two quantities
The same callback reports "fetched" and "enriched", so the second overwrites
the first and a later message quotes the wrong number. Catalogue: generic P19/P36.
Ask: *does each field the UI shows hold exactly one quantity for the whole run?*

---

## Review checklist

Run these for every diff touching data-path, I/O, external calls, or scoring:

- [ ] P1: Are retryable failures getting a retryable state, not a permanent negative?
- [ ] P2: Is every failure logged with a count? Is empty result distinguishable from failure?
- [ ] P3: Are all sibling sources/fields/tabs enumerated and handled?
- [ ] P4: Are years/dates/thresholds/topic-words in config, not code?
- [ ] P5: When one call is hardened, are all sibling calls hardened in the same change?
- [ ] P6: Is every status flag verified against the artifact it claims exists?
- [ ] P7: Does scoring/ranking reward the actual signal, not a proxy?
- [ ] P8: Does every server-side HTTP proxy validate scheme (`https://`) and reject private IPs?
- [ ] P9: Does every DOM insertion of external text use `textContent` or `createTextNode`, not `innerHTML`?
- [ ] P10: Is every run precondition enforced in shared code and tested from the web route, the GUI and monitor.py?
- [ ] P11: Does each data-changing stage have a test on the final output, run through the real pipeline order?
- [ ] P12: Does each live counter hold one quantity for the whole run?
- [ ] Common-flow run: before calling a feature done, run it in the real UI with an empty filter, a filter that matches nothing, and one that matches — and read what the screen says.

---

## Open risks

*Risks found by review but not yet triggered in production.*

- **PDF proxy SSRF (P8):** spec says "only fetch URLs from the database" but does not
  require a scheme/host allowlist. Any `http://`, `file://`, or private-IP URL stored
  via an ingestion source would be fetched server-side. Needs `https://`-only guard +
  private-IP block before the proxy route ships. (Found: spec review 2026-09-17)

- **XSS in paper detail modal (P9):** spec adds a modal that renders full abstract and
  key_findings from the server. If implemented with `innerHTML`, this is an XSS sink
  because abstracts originate from third-party APIs. Spec must mandate `textContent`
  for all modal content. (Found: spec review 2026-09-17)

---

## Misses

*Bugs that shipped through a Learning-QA pass — record to keep the catalogue honest.*

- **2026-09-18 — filter runs (P10, P11, P12).** Six review rounds before this date covered
  access codes, nonces and merges (security/concurrency). None ran a filter end to end.
  The web search tests replaced the orchestrator with a fake that never enriches. Generic P33
  (audit along one axis) and P26 (self-review blind spots).

---

## Fix log

*Newest first. Format: Issue → Root cause (Pn) → What would have caught it → Fix → Rule.*

- **2026-09-18 — Empty filter ran; enrichment wasted; wrong counts; Run button stateless.**
  - Issue: an empty saved filter ran a full search from the web app; runs that matched
    nothing still enriched every fetched paper; enriched fields never reached the results;
    "N were fetched" showed the enrichment count; the Run button gave no state and allowed
    parallel runs.
  - Root cause: empty-filter guard only in `gui.py` (P10); results snapshotted with
    `to_dict()` before `_enrich` ran, and `_enrich` not gated on the match (P11); `_enrich`
    reported through `on_progress`, overwriting `job.fetched` (P12); global rule "disable
    background-launch buttons" not applied.
  - What would have caught it: one real-UI run of an empty filter and a no-match filter; a
    test asserting an enriched field in the web results.
  - Fix: see `docs/implementation_plan_2026-09-18_filter_run.md`.
  - Rule: test each stage by its effect on the user's output; enforce run preconditions in
    shared code.
