# QA gate — implementation plan 2026-09-29 tiers 1–3, 2026-09-30

RANGE: origin/main...HEAD
COMMITS: 4 — 7ea410b (tier 1 + stored " (PubMed)" labels),
  991246e (W1.a baseline advance for T1.1), a92857b (tiers 2–3),
  5b06437 (W1.a baseline advance for tiers 2–3)
METHOD: adversarial review of the materialized diff (git diff origin/main...HEAD)
  plus the current tree, against docs/implementation_plan_2026-09-29_next.md and the
  ~/.claude/standards/learnings.md patterns. Each "done" item was checked two ways:
  (1) does the fix implement the plan's finding, and (2) does its named test fail on
  the pre-fix code. Each skip/block reason was checked against the code. The caller's
  adversarial probes were each exercised against the live code, not reasoned from the diff.
TESTS: venv/bin/python -m pytest tests/ -q -> 1652 passed, 2 skipped, 4 warnings,
  exit code 0.
VERDICT: REJECTED

APPLICABLE (of P1–P29): P13 (silent widening of a matching rule), P19
  (producer/consumer drift between the plan's test spec and the committed test), P26
  (fix commit introducing a subtler regression). The remaining items were clean.

CHECKED (done items — fix matches the finding, and the test fails without it):
  - Stored " (PubMed)" labels: _fix_stored_source_labels rewrites only
    TRIM(journal_or_server)='(PubMed)' -> 'PubMed' and trims leading/trailing space
    only where it exists; idempotent, logged. test_br14 asserts exact values
    ["PubMed", "Lancet (PubMed)", "Lancet (PubMed)", "bioRxiv"] and that the log line
    appears once, not on the second open. Fails on the pre-fix tree (no migration).
  - T1.3: SETTINGS_REFUSALS = {"Unpaywall"}; a non-Unpaywall Refused now appends to
    `unreachable` and reads "try again later". test_t13 asserts the OpenAlex and
    Semantic Scholar wording and that no "check its settings" text appears. Fails on
    the pre-fix tree (the old single "check its settings" branch).
  - T1.4: app.js findByTitle() returns null when neither localStorage nor the server
    default is known, and the checkbox/loaders use `!== false`; the route passes
    find_by_title=None through to _extract_text which applies the config itself.
    test_f1 adds (None,None,None) and ("off",None,False); test_t14 asserts seen["by_title"]
    is None. Both fail on the pre-fix tree (default was `true`).
  - T1.5a: sign_in_guard takes the slot before allow(); a 503 spends no attempt, and
    every path after a successful try_slot releases it in `finally`. test_t15a_a_busy_…
    and test_t15a_a_limited_… both fail on the pre-fix ordering.
  - T1.5b: reset_pin wraps the pin-clear + clear_llm_key + end_sessions + setup-code
    write in one transaction (commit=False helpers, rollback on any exception).
    test_t15b monkeypatches clear_llm_key to raise and asserts pin_hash + session_nonce
    unchanged and the session still valid. Fails on the pre-fix tree (end_sessions
    committed the pin clear mid-way).
  - T1.5c: _setting()/from_config guard int() and the section type with a warning +
    default. test_t15c covers non-integer, None, <=0, and a non-dict section. Errors
    on the pre-fix tree (int("lots")).
  - T1.6: summary_pdf_link returns pdf_url first, then best_oa_url only when
    _looks_like_pdf, else pdf_url with best cleared. test_t16 asserts the PMC landing
    page is NOT returned and that .pdf//pdf//article-pdf//pdf/ shapes and pdf_url are.
    Fails on the pre-fix tree (pdf_url returned the landing page).
  - T2.2: OllamaClient params default to None and are filled from llm_config.yaml only
    when one is None; OLLAMA_MODEL/OLLAMA_URL/OLLAMA timeout literals are gone.
    test_t22 is a grep+ast guard plus asserts bare-client config fallback and
    caller-wins. Fails on the pre-fix tree (the literal constants exist).
  - T2.3: pdf_handler and unpaywall route their User-Agent through the shared
    user_agent_with/polite_user_agent. test_t23 asserts "biorx/1.0 (mailto:…)" and the
    bare "biorx/1.0". Fails on the pre-fix tree ("biorx/1.0" hardcoded).
  - T2.4: load_filters keeps a list and warns on a duplicate name; --all runs both.
    test_t24 asserts both "New Filter" entries run and the warning fires. Fails on the
    pre-fix keyed-by-name path (one dropped).
  - T2.7: JobRegistry.submit raises TooManyJobs before key dedup when the owner's
    non-terminal jobs reach the config cap; submit_billed releases the reserved spend
    and returns 429 with reason="too_many_jobs"; app.js reads that reason so it is not
    the daily cap. test_t27_* cover the cap, config, release, and node-run page logic.
    Fails on the pre-fix tree (no cap, no reason field).
  - T3.2/T3.3/T3.4/T3.5/T3.8: each has an exact-value or behaviour test that fails on
    the pre-fix tree (fatal() names the uid; ast-based env scan sees BIORX_PDF_FONT;
    openalex_user_agent warns once; docs/ outside cycles/ is scanned; the three paths
    are git check-ignore'd). T3.1 and T3.6 are docstring/comment corrections with no
    runtime behaviour (correct as stated).

SKIP / BLOCK REASONS — verified true against the code:
  - T1.2 (skip): reference_list_items UNIQUE(list_id, doi) still exists (db.py:334),
    but its CRUD (db.py:1177-1231) is reached only by gui.py (gui.py:2154), which D1
    marks retiring. The web app's user_reference_list_items is UNIQUE(list_id, paper_id)
    (db.py:403) and INSERT OR IGNOREs on it (user_store.py:608). TRUE.
  - T2.5 (skip): a product decision (does discover get its own allowance), tied to D3.
    TRUE as a decision, not a code fact.
  - T2.6 (skip): summarize() returns a stored full_text summary with reused=True and
    makes no model call (src/summarize.py:226-234), so a paper with a full-text
    summary is never re-fetched; a re-fetch only follows a try that found no PDF.
    TRUE.
  - T2.1 (blocked on D2) and T3.7 (blocked on D3): both gate on owner decisions that
    are still open (D0–D3 "open — the owner's decisions"). Block is legitimate.

ADVERSARIAL PROBES (each run against the live code, not reasoned from the diff):
  - T1.5a slot leak on 429 / exception / generator close: PASS. try_slot() sits outside
    the try and release_slot() in `finally`, so exactly one release per successful
    acquire; the 429 path releases (test_t15a_a_limited_…), the 503 path never acquires,
    and GeneratorExit still runs the finally. No path leaks a slot.
  - T1.5b still ends sessions and clears keys for every merged account: PASS. The loop
    iterates merged_family(db, user_id) at every depth, clearing pin + key per uid and
    calling end_sessions (which re-walks the same family); pre-existing tests
    test_pc6_reset_pin_also_clears_merged_away_pins, test_pc6_reset_pin_reaches_every_merge_level,
    and test_m5_reset_clears_llm_key still pass. The connection uses sqlite3's default
    implicit-transaction isolation, so rollback is real (not autocommit).
  - T2.7 does not block a normal batch, releases spend, page not the cap: PASS. A
    "Summarize checked" batch awaits each paper sequentially (app.js:2708-2729), so at
    most one summary job is in flight; the cap is not reached by a normal batch. The
    refused summary path calls spend.release_unused (test_t27_route_… asserts it), and
    app.js:2790 distinguishes reason==="too_many_jobs" from the daily cap (node test).
  - T2.2 does not load config when all args are passed: PASS. `if None in (base_url,
    model, timeout, thinking)` guards _ollama_settings(); a fully-specified client never
    reads llm_config.yaml (and so cannot hit the no-ollama-section ValueError).
  - T1.6 does not skip a real PDF: PASS. pdf_url is always a real PDF (Unpaywall sets
    url_for_pdf, OSF sets links.pdf); when best_oa_url is a real PDF with pdf_url empty
    it is always a caught shape (arxiv "/pdf/", biorxiv ".full.pdf"). _looks_like_pdf
    never skips a real PDF produced by a current source.
  - Startup label migration touches only intended rows: PASS. The first UPDATE matches
    exactly TRIM(…)= '(PubMed)'; the second only rows with leading/trailing space, and
    only trims. "Lancet (PubMed)" and "bioRxiv" are untouched (test_br14 asserts it).
  - T1.1 dedup must not merge different papers more often: FAIL (see F1).

NOT COVERED:
  - Live provider / OA calls are mocked throughout; real HTTP is integration-only.
  - node-harness tests are skipped when node is absent (present here; they ran).
  - The browser smoke run the plan's §4 asks for on app.js/summaries changes was not
    re-run in this gate (the plan lists it as a follow-up for the common-flow check).

RANKED FINDINGS
===============

F1 (HIGH, CONFIRMED BY EXECUTION) — the T1.1 surname key over-merges "da Silva" and
  "Silva": two different first authors with the same title and year now collapse.
  File: src/sources/dedup.py:49-67 (_surname), test in tests/test_dedup.py:1057-1067.
  The plan's own test spec (plan §2 T1.1) read: "…a different 'Silva' paper does not
  [merge]." The committed test substitutes "de Souza" for the second author — a
  different surname that would not merge under ANY keying — and so never exercises the
  collision the plan called out.
  Proof (old vs new _surname over the actual key function):
      OLD  family "da Silva" -> "dasilva"    display "Ana Silva" -> "silva"   distinct
      NEW  family "da Silva" -> "silva"      display "Ana Silva" -> "silva"   identical
      Deduplicator: Europe PMC "da Silva" + arXiv "Ana Silva", same title+year -> len 1
  (probe output attached: family 'da Silva' -> silva; display 'Ana Silva' -> silva;
   "da Silva (EPMC) vs Silva (arXiv), same title+year -> len(d) = 1").
  The change makes every branch key on the surname's last word, so a leading lower-case
  particle ("da", "de", "van", "von") no longer distinguishes the surname: "da Silva"
  now collides with "Silva", "de la Cruz" with "Cruz". This is exactly "merging
  different papers (same title+year, different first author) more often" — a silent
  widening of the identity rule (P13). Before the change the family branch kept the
  full particle ("dasilva"), so the two did not merge.
  Impact: for records with no DOI/PMID/PMCID (the title-key fallback), two genuinely
  different papers sharing a title and year and whose first-author surnames differ only
  by a particle are collapsed into one, losing a paper and mis-attributing its summary
  or PDF.
  Fix direction: the key must keep the particle's presence so the intended merge
  ("da Silva" EPMC == "Ana da Silva" arXiv) still happens while "da Silva" != "Silva"
  — e.g. key on (last word, has-lowercase-particle) rather than the bare last word, or
  otherwise preserve the particle as a distinguishing bit. Add a regression test that
  asserts EXACTLY the plan's case: Europe PMC "Ana da Silva" vs arXiv "Ana Silva" must
  NOT merge (assert len == 2, not a floor).
  Confidence: high (reproduced with the real Deduplicator and adapters).
