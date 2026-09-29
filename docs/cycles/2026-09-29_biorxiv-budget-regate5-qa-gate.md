# Re-gate 5 — bioRxiv/medRxiv species title terms (2026-09-29)

Sixth learning-qa sweep over `origin/main...HEAD`. Re-gate 4 REJECTED on F9
(the chimeric-antibody exception existed in one word order only: "mouse human
chimeric" kept, but its mirror "human mouse chimeric" still dropped under
"Exclude animal studies", a human/therapeutic study wrongly excluded). Commit
b13b233 answers it by adding "human mouse chimeric" to
`animal_title_exceptions` (the `[\s-]+` join already covers hyphen and space),
and folds in re-gate 4's note about "rat liver microsomal" (the adjective
spelling of the listed noun "microsomes") by adding that phrase too. This gate
confirms F9 is fixed, probes both word orders and both spellings, confirms
F1–F8 stay fixed, mutation-checks the new must-keep assertions, and runs the
suite.

RANGE:       origin/main...HEAD  (origin/main = 63912ab, HEAD = b13b233)
             fix re-swept as its own range: 6c728a0..HEAD = b13b233 (per P26).
COMMITS:     8 — f854662 feat (budget + page limit), 2162447 test(drift) baseline,
                   7acd226 fix (F1 term list + F2 progress channel),
                   167045e test(drift) baseline advance,
                   108a6c7 fix(species): restore lab-animal words + exceptions (F3),
                   f52b58e fix(species): hyphen compounds, plural models, CHO-only (F4–F6),
                   6c728a0 fix(species): reagent prefixes in config, not a hyphen boundary (F7, F8),
                   b13b233 fix(species): chimeric exception in both word orders (re-gate 4 F9)
APPLICABLE:  P3, P4, P5, P7, P10, P26, P27
CHECKED:     P1 P2 P3 P4 P5 P6 P7 P10 P13 P19 P26 P27 (the exception list, the
             `[\s-]+` join, the reagent-prefix branch, `title_names_an_animal_study`
             and its `filter_papers` caller, the one reworked test, the fix range
             re-swept, and an independent probe of every F1–F9 phrase both ways).
NOT COVERED: security beyond the XSS sink (P8 SSRF untouched), concurrency,
             performance, authN/authZ; Europe PMC/PubMed query-time species
             clause (unchanged, outside this diff).
SUITE:       venv/bin/python -m pytest tests/ -q  →  1610 passed, 2 skipped (green).

## F9 answered on its named target — CONFIRMED

The mirror orientation now keeps, in both spellings, via
`filter_papers(..., {"species": "no-animal"})`:

- Must KEEP (3/3): "A human-mouse chimeric antibody in lymphoma patients",
  "Human mouse chimeric antibody therapy", "Rat liver microsomal metabolism of
  a new antiepileptic" (the adjective-spelling note, now an exception phrase).
- The original orientation still keeps (2/2): "A mouse-human chimeric
  antibody in lymphoma patients", "Mouse human chimeric antibody therapy".
- The mirror that must still drop does (1/1): "A human-mouse xenograft model
  of cancer" — a xenograft is an animal study, and the exception is scoped to
  the full phrase "… chimeric", so it does not strip the xenograft.

The two word orders are now symmetric ("mouse human chimeric" ↔ "human mouse
chimeric"), closing the exact drift F7's fix suggestion warned against.

## F1 / F2 / F3 / F4 / F5 / F6 / F7 / F8 stay fixed — CONFIRMED

Independent probe (not the suite's own tests) over the gate phrases:

- F1 (false exclusion of human studies): the eight gate titles (bovine serum
  albumin, porcine valve, rabbit monoclonal antibody, CHO, canine tooth, MICE,
  murine typhus, equine-assisted therapy) plus the re-gate keeps (mouse
  tracking, computer mouse, ratio, dog ownership, stress/strain,
  pirates/parrots, mousetrap-shaped) all KEEP — 15/15.
- F2 (progress channel): untouched by b13b233 (edits only
  filter_vocabulary.yaml, tests/test_filtering.py); green suite.
- F3 (bare singulars + model phrases): mouse/rat/hamster/macaque/rodent and
  porcine/rabbit model DROP — 7/7.
- F4 (reagent prefixes, hyphen/space/none): anti-mouse / anti-rat /
  anti-rodent / "anti mouse" / "antimouse" all KEEP — 5/5.
- F5 (plural models): bovine/ovine/canine/equine models DROP — 4/4.
- F6 (CHO-only): Chinese hamster ovary KEEPS, Syrian hamster ovary DROPS — 2/2.
- F7 (right-hyphen reagents, hyphen + space spellings): hybridoma, collagen,
  microsomes, homogenate, skin extract KEEP — 9/9.
- F8 (hyphenated animal titles + open compounds): knockout-/transgenic-/
  SCID-/nude-/wild-type-/germ-free- mouse, knockout-rat, and their open
  spellings DROP — 12/12.

## Mutation check (P27) — the new assertions are provable-failing

Removing just the two new exception phrases ("human mouse chimeric", "rat
liver microsomal") and re-running
`test_br7_hyphen_compounds_with_other_senses_are_kept` goes RED: the three new
titles drop out of the keep-list ("A human-mouse chimeric antibody…",
"Human mouse chimeric antibody therapy", "Rat liver microsomal metabolism…"),
and the exact-equality assertion fails on the first extra item. The assertions
guard the exact phrases they name; restoring the file returns it byte-identical
to b13b233 (`git diff` empty).

## FINDINGS

None. The fix is the single sanctioned change from re-gate 4's F9 (one
exception phrase + must-keep test), plus the note it also carried. No new
false negative of the "should still drop" kind was found: the phrase is scoped
to its full text, so a genuine animal study that also names the animal again
still drops ("A human-mouse chimeric mouse model of breast cancer" DROPS, "Rat
liver microsomal enzyme induction in rats" DROPS).

## Notes (not findings)

A title-only word check is a literal list, so it is never complete; these are
single missing phrases, not regressions or failures of a stated rule, and are
consistent with the documented precision-over-recall stance (the vocabulary
comment: "not dropping human studies" is the thing the design exists to
protect, at the price of missing some livestock/veterinary titles).

- "Human-mouse hybridoma" still drops (the list has "mouse mouse hybridoma"
  but not its human-mouse counterpart). A heterohybridoma is a reagent/cell
  line, same class as F9; it is a single missing phrase in the same family,
  not a new defect. Not flagged per the stance.
- "Rat hepatic microsomes" (adjective on the *organ* rather than the prep)
  still drops, like any "rat …" phrase not in the list. Same literal-list
  limitation, single phrase.
- "A human-mouse chimeric model" (a humanized-mouse model with no second
  animal word) now keeps. This is symmetric with the already-accepted
  "mouse-human chimeric model" gap and is the accepted price of not dropping
  chimeric-antibody/therapeutic studies; a second animal word in the title
  still catches the animal study (probed above).

## VERDICT: APPROVED

F9 is fixed in both word orders and both spellings, the "rat liver microsomal"
note is folded in, F1–F8 all stay fixed under an independent probe, the new
assertions are mutation-proven to fail without the fix, and the suite is green
(1610 passed, 2 skipped). No regression and no clear failure of a stated rule
remain; the residual gaps are single phrases within the documented
precision-over-recall stance, recorded as notes.
