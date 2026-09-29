# Re-gate 3 — bioRxiv/medRxiv species title terms (2026-09-29)

Fourth learning-qa sweep over `origin/main...HEAD`. Re-gate 2 REJECTED on F4
(hyphen compounds re-opened F1's silent false-exclusion class), F5 (plural
model-phrase gap) and noted F6 (over-broad "hamster ovary cells" exception).
f52b58e answers all three: the left-hyphen boundary, four plural models, and
removing "hamster ovary cells". This gate confirms each is fixed, probes both
directions adversarially (human studies wrongly dropped; animal studies wrongly
kept, including hyphen-on-the-right titles like "Mouse-derived organoids"),
confirms F1–F3 stay fixed, and runs the suite.

RANGE:       origin/main...HEAD  (origin/main = 63912ab, HEAD = f52b58e)
             fix re-swept as its own range: 108a6c7..HEAD = f52b58e (per P26).
COMMITS:     6 — f854662 feat (budget + page limit), 2162447 test(drift) baseline,
                   7acd226 fix (F1 term list + F2 progress channel),
                   167045e test(drift) baseline advance,
                   108a6c7 fix(species): restore lab-animal words + exceptions (F3),
                   f52b58e fix(species): hyphen compounds, plural models, CHO-only (F4–F6)
APPLICABLE:  P3, P4, P5, P7, P10, P26
CHECKED:     P1 P2 P3 P4 P5 P6 P7 P10 P13 P26 P27 (matcher, exception strip,
             filter_papers caller, the two new tests, re-swept fix range,
             differential probe old-vs-new boundary).
NOT COVERED: security beyond the XSS sink (P8 SSRF untouched), concurrency,
             performance, authN/authZ; Europe PMC/PubMed query-time species
             clause (unchanged, outside this diff).
SUITE:       venv/bin/python -m pytest tests/ -q  →  1610 passed, 2 skipped (green).

## F4 / F5 / F6 answered on their named targets — CONFIRMED

F4 (hyphen compounds): the left boundary is now `(?<![\w-])`, so a term
prefixed by a hyphen is not read as the animal. Probe via
`filter_papers(..., {"species": "no-animal"})`:

- Reagents KEEP (9/9): anti-mouse antibody panel, anti-rat IgG, anti-rodent
  antibody; rat-bite fever / rat bite fever, rat lungworm, mouse-ear cress /
  mouse ear cress, rat race, rodenticide poisoning.
- The exception list strips the disease/plant/idiom senses before the check;
  "Rodenticide poisoning" still keeps because "rodent" is followed by "i".

F5 (plural models): bovine/ovine/canine/equine models added; all seven
"<animal> model(s)" families now carry both numbers. "Bovine models",
"Ovine models", "Canine models", "Equine models" all DROP (4/4).

F6 (CHO-only): "hamster ovary cells" removed. "Chinese hamster ovary cells"
KEEPS (the cell line), "Syrian hamster ovary cells after infection" DROPS
(the animal-tissue study). 2/2.

The new tests `test_br7_hyphen_compounds_with_other_senses_are_kept` and
`test_br7_hyphen_on_the_right_and_plural_models_still_drop` pin both directions
and pass.

## F1 / F2 / F3 stay fixed — CONFIRMED

F1 (false exclusion of human studies): all eight gate titles (bovine serum
albumin, porcine valve, rabbit monoclonal antibody, CHO, canine tooth, MICE,
murine typhus, equine-assisted therapy) plus the re-gate keeps (mouse-tracking,
computer mouse, ratio, dog ownership, stress/strain, pirates/parrots,
mousetrap-shaped) and the documented gaps (bovine mastitis, canine
osteoarthritis, equine laminitis, murine leukemia virus) KEEP — 28/28 in the
probe. F3 (bare singulars): mouse/rat/hamster/macaque/rodent titles and the
model phrases DROP — 25/25. F2 (progress channel) is untouched by f52b58e
(which edits only filter_vocabulary.{py,yaml}, tests/test_filtering.py, docs);
test_br3_progress_counts_matches… passes in the green suite.

## FINDINGS (ranked)

F8 · P26/P7 (fix commit introduces a regression; bad trade) ·
    src/filter_vocabulary.py:197 + filter_vocabulary.yaml:166-169 ·
    The left-hyphen boundary `(?<![\w-])` fixes "anti-mouse" by treating ANY
    hyphen-prefixed animal word as non-animal. But the hyphenated
    compound-adjective spelling of the commonest animal-study titles is also
    hyphen-prefixed, so they now LEAK through "exclude animal studies":
    7/7 KEPT — "knockout-mouse model", "transgenic-mouse studies",
    "SCID-mouse xenograft", "nude-mouse xenograft", "wild-type-mouse
    controls", "germ-free-mouse colonization", "knockout-rat model".
    Differential probe (git show f52b58e^:…exec'd, pitfall 3): OLD boundary
    `(?<!\w)` DROPPED all seven; NEW `(?<![\w-])` KEEPS all seven. This is a
    regression, not a documented gap, and it trades a rarer reagent
    false-positive (anti-mouse antibody) for a far more common animal-study
    false-negative (knockout/transgenic mouse models are the bread-and-butter
    of the animal literature). The open-compound spellings ("knockout mouse
    model") still drop, so the leak is spelling-sensitive. ·
    Fix (cheap): exempt only the reagent/negation prefixes, not any hyphen —
    e.g. require `anti-`/`non-` before the animal word rather than any `-`,
    or add the compound-adjective animal forms as must-drop tests and match
    them explicitly. Pin with "A knockout-mouse model" as a must-drop test. ·
    confidence: high · severity: high (core purpose of the species filter —
    exclude animal studies — weakened on the most common construction).

F7 · P7 (same class as F4, other side of the hyphen) ·
    filter_vocabulary.yaml:172-182 ·
    The right-hyphen side is still a boundary, so human-sense compounds where
    the animal word sits LEFT of the hyphen are read as animal studies: 6/6
    DROPPED — "mouse-human chimeric antibody", "mouse-mouse hybridoma",
    "rat-tail collagen", "rat-liver microsomes", "rat-brain homogenate",
    "mouse-skin extract". These are reagents/therapeutics/homogenates, the
    same class F4 killed on the left ("anti-mouse antibody"), just the mirror
    orientation. The exception list handles one right-hyphen plant
    ("mouse-ear cress") but not the reagent/chimeric class. Pre-existing
    (old boundary dropped them too), so not a regression, but F4 claimed to
    close the "reagent/hyphen compound" class and only closed the left half. ·
    Fix (cheap): add exception phrases (mouse-human chimeric, rat-tail
    collagen, mouse-mouse hybridoma, rat-liver microsomes) or, better, share
    one rule for both orientations so neither side drifts. ·
    confidence: medium · severity: medium (silent drops of legitimate
    non-animal studies; rarer than F1/F4 originals but the same defect class).

## Notes (not findings)

- "Mouse-derived organoids" / "Rat-specific gene expression atlas" still DROP
  (right-hyphen animal titles preserved by the asymmetric boundary) — the
  direction the task asked to confirm, and it holds.
- The exception phrases and the acronym rule (MICE/RATS kept) still behave as
  in re-gate 2; no change on those axes.
- "Anti mouse antibody" (space, not hyphen) still DROPS — the fix is
  spelling-sensitive. Folded into F7's orientation point, not a separate
  finding.

## VERDICT: REJECTED

F4/F5/F6 are answered on their named targets (9/9 reagents keep, 4/4 plural
models drop, CHO/Syrian-hamster split 2/2), F1–F3 stay fixed (28/28 keep,
25/25 drop), and the suite is green (1610 passed, 2 skipped). The rejection is
on F8 and F7, both from the same root: the F4 boundary change is asymmetric,
so one side of the hyphen now leaks the commonest animal-study titles
(knockout-mouse / transgenic-mouse — a measured regression) and the other side
still drops legitimate reagents (mouse-human chimeric antibody). F8 is a
regression introduced by the very fix being gated, verified by differential
probe; F7 is the F4 class left half-closed. Both need the hyphen rule made
symmetric or prefix-scoped, with must-drop and must-keep tests on each side.
