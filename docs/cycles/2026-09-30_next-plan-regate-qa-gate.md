# QA gate — re-gate of the 2026-09-30 F1 rejection

RANGE: origin/main...HEAD
COMMITS: 6 — 7ea410b (tier 1 + stored " (PubMed)" labels),
  991246e (W1.a baseline advance for T1.1), a92857b (tiers 2–3),
  5b06437 (W1.a baseline advance for tiers 2–3), a9cfc35 (F1 fix:
  keep surname particles in the title key), b099f5e (W1.a baseline
  advance for the surname-particle fix)
PRIOR GATE: docs/cycles/2026-09-30_next-plan-qa-gate.md rejected on F1 only
  (the T1.1 surname key merged Europe PMC "da Silva" with arXiv "Ana Silva").
  This gate re-checks F1 against the fix a9cfc35 and confirms every other
  item the prior gate found clean is still clean.
METHOD: the fix commit was reviewed two ways — (1) does it implement the plan's
  finding (T1.1: "a different 'Silva' paper does not [merge]"), and (2) do its
  named tests fail on the pre-fix code. The plan's exact case and every intended
  merge were exercised against the live code with the real Deduplicator and the
  real Europe PMC / arXiv / bioRxiv adapters (not reasoned from the diff).
  Adversarial probes were run against the live _surname, not the diff.
TESTS: venv/bin/python -m pytest tests/ -q -> 1658 passed, 2 skipped, 4 warnings,
  exit code 0 (prior gate: 1652 passed; +6 from the two new T1.1 tests).
VERDICT: APPROVED

APPLICABLE (of P1–P29): P13 (silent widening of a matching rule) — resolved:
  the fix narrows the key, it does not widen it. P26 (fix commit introducing a
  subtler regression) — checked explicitly; the fix is a strict refinement of
  the key space and cannot merge two previously-distinct keys. P19 (producer/
  consumer drift) — clean: the committed tests now assert the plan's own case.
  Nothing else applicable.

F1 RESOLVED
===========
The plan's exact case now holds: Europe PMC family "da Silva" and arXiv "Ana
Silva" (same title "Agents That Simulate Societies", same year) stay TWO records
(probe: len == 2). The pre-fix code merged them (probe on 5b06437's _surname:
family "da Silva" -> "silva", display "Ana Silva" -> "silva", identical). The
fix a9cfc35 makes the no-comma display branch key on the last word plus the
lower-case particles immediately before it, so:

    family  "da Silva"   -> "dasilva"   (unchanged from the pre-7ea410b code)
    display "Ana da Silva" -> "dasilva"  (now matches the family branch)
    display "Ana Silva"  -> "silva"      (stays distinct)

INTENDED MERGES STILL HAPPEN (probe, real adapters):
  - family "da Silva" (Europe PMC) + display "Ana da Silva" (arXiv)  -> len 1
  - family "da Silva" (Europe PMC) + "da Silva, A." (bioRxiv comma)  -> len 1
  - the multi-source merge test (Europe PMC + arXiv + bioRxiv all "da Silva")
    still passes (test_t11_multi_word_surname_merges_across_sources).

REGRESSION TESTS ADDED (a9cfc35, tests/test_dedup.py):
  - test_t11_da_silva_and_silva_are_not_merged — asserts EXACTLY len == 2 for the
    plan's case (Europe PMC "da Silva" + arXiv "Ana Silva"), not a floor. Fails on
    the pre-fix tree (returns 1).
  - test_t11_surname_keys — 5 parametrized exact-equality cases (==, not floor):
    ("da Silva","Ana da Silva",True), ("van der Berg","Jan van der Berg",True),
    ("da Silva","Ana Silva",False), ("de la Cruz","Maria Cruz",False),
    ("Smith","John Smith",True). Fails on the pre-fix tree for the False rows.

ADVERSARIAL PROBES (each run against the live _surname / Deduplicator):
  - No NEW over-merge: the change is a strict refinement. The family branch
    returns to the full surname (was last-word in 7ea410b/a92857b) and the
    display branch adds particles to the last word. Both only split keys, so no
    pair that was distinct before becomes identical. family "da Silva" (dasilva)
    vs "Silva" (silva) vs display "Ana Silva" (silva) vs "Ana da Silva" (dasilva)
    are all pairwise correct.
  - Capitalised particle "Van" -> UNDER-merge only (safe direction): display
    "Ludwig Van Beethoven" keys "beethoven" (capital "Van" is not a lower-case
    particle, so it is not kept) and does NOT match family "Van Beethoven"
    ("vanbeethoven"). This matches the plan's literal "lower-case particle" spec;
    it errs toward failing to merge, never toward merging different papers. Note:
    the "beethoven" key it does produce collides with a bare "Beethoven" surname —
    but that last-word fallback is the pre-existing behaviour, not introduced here.
  - Multi-word surname with an internal capitalised word ("da Silva dos Santos")
    -> UNDER-merge only: display "Maria da Silva dos Santos" keys "dossantos"
    (particles adjacent to the last word) while family "da Silva dos Santos" keys
    the full "dasilvadossantos". Same paper fails to merge. Outside the plan's
    scope (T1.1 was the single-particle "da Silva" case) and in the safe direction.
  - Lower-case middle word absorbed as a false particle -> pathological, low risk:
    display "Ana maria Silva" keys "mariasilva" (an all-lower-case middle given name
    would be treated as a particle). Real given names are capitalised, so this is
    not a practical path; noted, not a defect for this gate.
  - Single-word names -> correct: display "Silva" -> "silva", family "Silva" ->
    "silva" (merge); display "da Silva" (two words, no given name) -> "dasilva".
  - bioRxiv "Surname, I." lists -> correct: the comma branch keeps the whole part
    before the comma, so "da Silva, A." -> "dasilva" and "Silva, A." -> "silva".
    The bioRxiv adapter itself sets family=split(",")[0] ("da Silva"), which the
    family branch keys identically.

OTHER ITEMS FROM THE PRIOR GATE
===============================
The only files changed since the prior gate's HEAD (5b06437) are
src/sources/dedup.py, tests/test_dedup.py, tests/web/test_no_retrieval_drift.py,
and docs/TODO (verified with git diff --stat 5b06437..HEAD). None of the other
done items (PubMed label migration, T1.3, T1.4, T1.5 a/b/c, T1.6, T2.2–T2.7,
T3.1–T3.8) touched code, and every one of their named tests is in the 1658
passing. The prior gate's skips/blocks (T1.2, T2.5, T2.6, T2.1/T3.7 blocked on
D2/D3) are unchanged.

W1.a BASELINE ADVANCE (b099f5e)
===============================
The advance a92857b -> a9cfc35 is correct and deliberate: dedup.py is in the
PROTECTED list, it changed in a9cfc35 (the last protected-file change in the
range), and the drift guard test_retrieval_modules_unchanged_since_the_web_work_began
now diff empty (4/4 drift tests pass). The commit message documents the W1.a
exception as required.

NOT COVERED:
  - Live provider / OA calls are mocked throughout; real HTTP is integration-only.
  - The capitalised-particle and multi-particle-internal-cap cases above are
    under-merges only and were characterised, not fixed; they are outside the
    plan's T1.1 scope and lie in the safe (fail-to-merge) direction.
