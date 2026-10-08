# QA Gate — today's gate TODO items (2026-10-07, TD1–TD11)

**RANGE:** `git diff origin/main..HEAD` (base `9458166`)
**COMMITS:** cddc90f (plan), ee3e301 (TD1–TD11 fixes), 2e1d372 (drift baseline advance)
**Plan:** `docs/implementation_plan_2026-10-07_gate_todos.md`
**Reviewer:** learning-qa failure-pattern sweep (cold, self-contained — read the
diff, traced the contracts through the modules, and ran independent probes; did
not see how the code was written)
**Test suite:** `venv/bin/python -m pytest tests/ -q` → **1783 passed, 2 skipped** (green)

## Verdict: APPROVED

All six gate checks pass, the full suite is green, and no finding rises to
blocking. One LOW finding is recorded for the backlog: a wildcard on a single
punctuation/hyphen-containing token (`kin-select*`, `COVID-19*`) drops the `*`
in the sent query, so the query is narrower than the local filter for that exact
input. It is not a regression — before TD1 that same token reached Europe PMC as
unquoted query syntax (`kin-select*` parsed as `kin` NOT `select*`) — and it is
an explicitly documented, debug-logged trade-off of the approved plan (TD1), not
a defect in its implementation.

---

## APPLICABLE

P2 (silent drop), P5 (sibling call paths — PubMed shares the Europe PMC builder;
Discover shares `wildcard_pattern`/`normalise_text`; the monitor and the route
share `fixed_dates`), P7 (adversarial — query injection, phrase spanning fields),
P19 (producer/consumer drift — local filter vs Discover counts vs source
queries), P26 (fix commit — the drift-baseline advance is itself a retrieval
edit that must be re-swept), P27/P35 (guard-the-guard — a bad baseline must not
silently skip), P28 (test→real-artifact isolation — the concurrency test must
not leak state onto the shared orchestrator), P29 (floor vs exact assertions —
tests must pin `==`, not `>=`).

## CHECKED

### 1. Local filter, Discover counts, and Europe PMC/arXiv queries agree on a term — PASS

The punctuation/hyphen change (TD4) has exactly one producer:
`normalise_text` (src/search_terms.py:37), and it is applied to **both** the
term and the text in every consumer:

- Local filter: `term_matches` (src/filtering.py:134) normalises each AND part
  and each field before `match_term`.
- Discover counts: `count_term_hits` (src/discover.py:175) normalises
  `title`/`abstract` separately and each part before building patterns.
- Source queries: `_needs_quotes`/`_term_clause` (query_builder.py) quote any
  punctuation-carrying part, so Europe PMC/arXiv read it as words, matching the
  filter's literal reading (TD1).

The one-field-per-phrase change (TD5) is applied identically in both local
paths: `term_matches` requires `any(match_term(p, t) for t in fields)` per part
— each part within one field, parts may be in different fields — and
`count_term_hits` mirrors it with `any(pt.search(f) for f in fields)`. The
wildcard is the shared `wildcard_pattern` (filtering.py:167) used by both
`match_term` and Discover's `_whole_word_pattern` (discover.py:151), so the two
cannot drift (P19, pitfall 2). `test_td4_hyphen_matches_space` (10 cases, both
directions, `*` and `+` kept literal) and
`test_td4_td5_discover_counts_follow_the_filter` pin the shared behaviour.

### 2. Quoting cannot be bypassed (query injection) — PASS

`_needs_quotes` (query_builder.py:47) quotes a part containing a space, any of
`:()[]{}^~?\\/+-!&|"*`, or exactly `AND`/`OR`/`NOT`/`ANDNOT`. `_quoted` escapes
`\` first, then `"`. Every clause carries a field prefix (`TITLE:`/`ABSTRACT:`/
`TITLE_ABS:`/`ti:`/`abs:`/`all:`), so a term cannot inject a field or a bare
operator. Verified two ways:

- `test_td1_injection_stays_inside_one_clause` (adversarial P7): the built query
  for `x) OR (TITLE_ABS:*` has only quoted spans and `AND` outside quotes.
- Independent probe over an injection battery — `x) OR (TITLE_ABS:*`,
  `foo" OR (TITLE_ABS:*) OR ("bar`, `a\\" OR ABSTRACT:`, `OR NOT AND`,
  `say "hi" there`, `x^2 + y[z] {q} ~ ? / \\ !` — every one produced only
  `TITLE_ABS:"…"` quoted clauses with no bare `)`, `(`, `OR`, `:`, or `"` escape.

### 3. arXiv wildcard handling is a superset of the local filter — PASS

`_arxiv_clause` (query_builder.py:362) keeps a lone wildcard part (`cooperati*`
→ `all:cooperati*`) and, inside an AND term, drops wildcard parts only when at
least one plain part remains (`cooperati* AND survival` → `all:survival`).
Any paper the local filter keeps (both `cooperati*` and `survival` present)
necessarily contains the plain part, so the arXiv query is a superset and the
local filter re-checks every part (TD3). Probe confirmed the four shapes:
`all:cooperati*`, `all:survival`, `all:survival`, `(all:cooperati* AND all:surviv*)`.
`test_td3_arxiv_keeps_wildcard` and
`test_td3_arxiv_and_term_drops_wildcard_parts_when_others_remain` pin them.

### 4. `fixed_dates` preserves legacy and explicit ranges — PASS

`fixed_dates` (src/filtering.py:216) normalises **first** — converting legacy
`date_from`/`date_to` to `start_date`/`end_date` — then calls `get_date_range`,
which prefers explicit dates and falls back to `days_back` only when unset. The
legacy-first ordering is load-bearing: normalising after reading would replace
`date_from` with `days_back`'s window, and `test_b13_legacy_filter_normalised`
(existing) plus the new `test_td7_fixed_dates_keeps_legacy_and_explicit_ranges`
both pass. Probe confirmed: legacy `2020-01-01`/`2020-12-31` preserved; explicit
`2021-03-01`/`2021-04-01` preserved; `days_back:10` → today-10/today.

### 5. The concurrency test is not vacuous — PASS

`test_ds2_counts_are_not_shared_between_concurrent_searches` (test_orchestrator.py:1048)
is a real two-thread run: two searches on one shared orchestrator, a
`threading.Barrier(2)` inside the shared PubMed adapter forces both searches to
be mid-source simultaneously, and each search gets a different overlap (one runs
Europe PMC→PubMed, the other PubMed alone). It asserts exact status strings
(P29 — `==` on the full line, not a substring or floor) and, crucially,
`set(vars(orch)) == before` — no per-search state (`counts`, `sent_here`,
`dedup`) may survive on the orchestrator. A shared counter would produce
`PubMed: 30 fetched` for the "both" search (or vice-versa) and fail the exact
assertions; the state-leak check catches the mutation the plan names directly.
A barrier timeout raises `BrokenBarrierError`, which fails the test rather than
passing silently.

### 6. Replaced/updated older tests are justified, not weakened — PASS

- **TD3** replaced two `test_batch_d.py` tests that asserted the arXiv wildcard
  is stripped. That claim was false: measured live (`all:cooperati*` 50 vs
  stripped 0; `ti:stress*` 583 vs 572), stripping never helped. The replacement
  asserts the wildcard is *kept*, and documents the measurements inline.
- **TD10** replaced the prior concurrency test that "partly checked source text"
  with the real two-thread test above — strictly stronger.
- **TD6** corrected `test_b7_servers_configurable` and
  `test_i_biorxiv_medrxiv_retries_on_5xx` to use the real
  `publication_sources.biorxiv_medrxiv.servers` shape; the old tests used the
  same wrong top-level shape the adapter read, which is why the bug survived.
  `test_td6_servers_read_from_the_shipped_config_shape` loads the real YAML.

## NOT COVERED

- TD1 live Europe PMC / TD3 live arXiv / TD4 live "kin selection" checks are
  recorded in the plan and `spec_coverage_webapp.md` as done (200/200, 5/5) but
  were not re-run from this machine — no live network calls in the gate.
- The two skipped tests are environmental and out of scope:
  `test_fulltext.py::…` (RLIMIT_AS is Linux-only) and
  `test_h_environment.py::test_h_runs_on_the_supported_python` (runs only in CI).

## FINDINGS

### L1 · P2/P19 · LOW (pre-existing trade-off, documented) · src/sources/query_builder.py:79-82

A wildcard on a single punctuation/hyphen-containing token loses the `*` in the
sent query. `_term_clause("kin-select*")` strips the `*`, finds the stem
`kin-select` must be quoted (contains `-`), and — because `stem.split()` is one
word, not the multi-word branch — emits `TITLE_ABS:"kin-select"` (exact phrase,
no wildcard). The local filter, however, normalises `kin-select*` to
`kin select*` and keeps any paper containing a `kin select…` prefix (e.g.
"kin-selection"). So the query is strictly narrower than the filter for that
input, and the divergence is logged only at DEBUG.

Not blocking: (a) before TD1 the same token was *worse* — sent unquoted,
`kin-select*` parsed as `kin` NOT `select*`, silently negating; (b) the plan
approves it ("a part that must be quoted loses the `*` (logged)"); (c) it is a
rare input (a wildcard immediately following punctuation). Backlog follow-up:
make the wildcard split honour hyphens as word breaks too (send `kin-select*` as
`(TITLE_ABS:kin AND TITLE_ABS:select*)`, the multi-word behaviour), so the query
stays a superset as it does for the space-separated case.

### N1 · clarity · NIT (non-blocking) · web/routes_searches.py:115

`filter_dict = fixed_dates(normalise_filter(filter_dict))` normalises twice —
`fixed_dates` itself calls `normalise_filter` (filtering.py:228). Idempotent and
harmless; optional cleanup.

---

## Acceptance criteria

| ID | Criterion | Result |
|---|---|---|
| TD1 | punctuation/operator/`OR` terms quoted; `adolescen*` unchanged; injection stays in one clause | 7 parametrized cases + adversarial test, green |
| TD2 | `is_and_term` removed | `src/search_terms.py` no longer defines it; suite green |
| TD3 | arXiv wildcard sent; dropped only when a plain part remains (superset) | 2 tests + replaced batch_d tests, green |
| TD4 | hyphens/punctuation match spaces, both directions, both filter and Discover | 10 cases + Discover test, green |
| TD5 | phrase does not span title/abstract; AND parts may | filter + Discover + search-within tests, green |
| TD6 | servers read from `publication_sources` shape | adapter + batch_i + shipped-config tests, green |
| TD7 | one date window per search; legacy + explicit + days_back | route + adapter + filtering tests + `test_b13`, green |
| TD8 | `resolve_active_sources` public, old alias kept | `test_td8_old_name_still_works`; BW5 tests, green |
| TD9 | re-send after merge counted a repeat, not "earlier source" | `test_td9_resend_after_merge_is_a_repeat`, green |
| TD10 | concurrency test non-vacuous, state-leak checked | `test_ds2_counts_are_not_shared_between_concurrent_searches`, green |
| TD11 | DS1 table corrected (six lines) | `docs/implementation_plan_2026-10-07_duplicate_status.md` |
| drift | baseline advanced once; guard still sound | `test_no_retrieval_drift` 4 tests pass, empty protected diff at HEAD |
| full suite | `venv/bin/python -m pytest tests/ -q` | **1783 passed, 2 skipped** |

---

## Notes

- The loop's own history is visible here: the title-abs gate recorded
  "L1 — `kin-selection` does not match `kin selection` locally" as a TODO; TD4
  in this batch fixes exactly that via the shared `normalise_text`.
- No fix commit was introduced, so there is no new unreviewed code to re-sweep.
  The two findings are LOW/NIT and go to the backlog; the stopping condition —
  no finding of MEDIUM or higher requiring a fix — is met.
