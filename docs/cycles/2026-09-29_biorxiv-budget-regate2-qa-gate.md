# Re-gate 2 — bioRxiv/medRxiv species title terms (2026-09-29)

Third learning-qa sweep over `origin/main...HEAD`. The first gate
(`2026-09-29_biorxiv-budget-qa-gate.md`) REJECTED f854662 on F1 (species title
terms dropping human studies) and F2 (progress channel mixing). The re-gate
(`2026-09-29_biorxiv-budget-regate-qa-gate.md`) confirmed F1/F2 fixed and
REJECTED on F3 alone: the F1 fix had dropped the bare singulars mouse/rat/
rodent/macaque/hamster, so the commonest animal-study titles passed. 108a6c7
restores those singulars and adds `<animal> model` phrases plus exception
phrases. This gate confirms F3 is answered on its named targets, re-probes both
directions adversarially, confirms F1/F2 stay fixed, and runs the suite.

RANGE:       origin/main...HEAD  (merge-base diff; origin/main = 63912ab, HEAD = 108a6c7)
             fix re-swept as its own range: 167045e..HEAD = 108a6c7 (per P26).
COMMITS:     5 — f854662 feat (budget + page limit), 2162447 test(drift) baseline,
                   7acd226 fix (F1 term list + F2 progress channel),
                   167045e test(drift) baseline advance,
                   108a6c7 fix(species): restore lab-animal words + exceptions (F3)
APPLICABLE:  P3, P4, P5, P7, P10, P26
CHECKED:     P1 P2 P3 P4 P5 P6 P7 P10 P13 P26 P27 (matcher, exception strip,
             filter_papers caller, the two new tests, re-swept fix range)
NOT COVERED: security beyond the XSS sink (P8 SSRF untouched), concurrency,
             performance, authN/authZ; Europe PMC/PubMed query-time species
             clause (unchanged, outside this diff).
SUITE:       venv/bin/python -m pytest tests/ -q  →  1608 passed, 2 skipped (green).

## F3 answered on its named targets — CONFIRMED

The bare singulars mouse/rat/rodent/hamster/macaque are back, and `<animal>
model` phrases cover the collision-prone names (bovine/porcine/rabbit/canine/
equine/ovine/murine). Exception phrases (mouse tracking, computer mouse,
Chinese hamster ovary) are stripped before the check; an all-caps match is
read as an acronym. Adversarial probe via `filter_papers(..., {"species":
"no-animal"})`:

- MUST DROP — 25/25 animal titles dropped: the eight F3 regression singulars
  (Mouse embryonic stem cell differentiation, Mouse brain development, Rat
  liver regeneration, Hamster model of diet-induced obesity, Macaque visual
  cortex recordings, A rodent study of sleep loss) plus model phrases (Porcine
  model, A rabbit model, Bovine/Ovine/Canine/Equine/Murine model), plurals
  (mice, rats, rodents, macaques), and the rest (zebrafish, Drosophila,
  Caenorhabditis, C. elegans, non-human/nonhuman primates, piglets, cattle).
- F1's eight gate titles still KEEP (bovine serum albumin, porcine
  bioprosthetic valve, rabbit monoclonal antibody, Chinese hamster ovary
  cells, canine tooth, MICE, murine typhus, equine-assisted therapy), plus the
  re-gate's additional keeps (Mouse-tracking, Computer mouse, Ratio, Dog
  ownership, Separating stress from strain, Pirates/parrots, Mousetrap-shaped)
  and the documented gaps (Bovine mastitis, Canine osteoarthritis, Equine
  laminitis, Murine leukemia virus). 19/19 kept.

F3 is answered: the modal animal-study titles now drop. The new tests
test_br7_singular_lab_animals_are_dropped and
test_br7_exception_phrases_and_known_gaps pin both directions.

## F1 / F2 stay fixed — CONFIRMED

F1 (false exclusion of human studies by the species title check): all eight
gate titles and the eleven additional probes survive; the acronym rule still
keeps MICE/RATS; the exception phrases keep the word-sense collisions that the
first fix addressed. No regression on the F1 axis for the cases the earlier
gates named.

F2 (progress channel): 7acd226's on_progress(matches)/on_status(papers read)
split is untouched by 108a6c7 (which edits only filter_vocabulary.{py,yaml},
tests/test_filtering.py, and two docs). test_br3_progress_counts_matches…
still passes in the green suite.

## FINDINGS (ranked)

F4 · P7 (re-opens F1's false-exclusion class via hyphen boundaries) ·
    src/filter_vocabulary.py:195 + filter_vocabulary.yaml:132/134/136 ·
    Restoring the bare singulars re-introduces the exact silent-drop class F1
    killed, through the whole-word boundaries: `(?<!\w)(?!\w)` treats `-` as a
    boundary, so hyphen-joined compounds match the inner word. Probe: 7/7
    non-animal/human titles DROPPED — "anti-mouse antibody" / "anti-rat IgG" /
    "anti-rodent antibody" (reagents, the same class as F1's must-keep "rabbit
    monoclonal antibody"), "rat bite fever" and "rat lungworm
    angiostrongyliasis" (human disease/parasite), "mouse-ear cress" (plant),
    "the rat race" (human psychology). The exception list enumerates only
    mouse-tracking/computer-mouse/CHO and misses the whole hyphen-compound
    class. F3's stated premise ("mouse/rat/rodent have no collision senses")
    was false: "rat" has a disease sense (rat-bite fever) and "mouse"/"rat"/
    "rodent" have a reagent sense (anti-<animal> antibody). ·
    Fix (cheap): make `-` a word character in the boundaries —
    `(?<![\w-])(?:…)(?![\w-])` — and add "rat race"/"rat-bite fever"/"rat
    lungworm" as exception phrases; add these as must-keep adversarial tests. ·
    confidence: high · severity: medium (silent drops of legitimate non-animal
    studies; rarer than F1's originals but the same defect class).

F5 · P5 (inconsistent sibling handling) ·
    filter_vocabulary.yaml:158-161 ·
    Model phrases are inconsistent across siblings: rabbit/porcine/murine carry
    plural forms ("rabbit models", "porcine models", "murine models"), but
    bovine/ovine/canine/equine are singular only. Whole-phrase `(?!\w)` means
    "bovine models" does not match "bovine model" (the trailing "s" is a word
    char). Probe: 4/4 MISSED — "bovine models", "ovine models", "canine
    models", "equine models" are kept (the plural-model gap). Singular forms
    drop correctly. ·
    Fix (cheap): add the four plural forms; assert "bovine models" in a
    must-drop test. · confidence: high · severity: low (recall direction, the
    side the docs already trade away).

F6 · P7/P4 (over-broad exception) ·
    filter_vocabulary.yaml:169 ·
    "hamster ovary cells" is broader than the CHO cell line it names ("Chinese
    hamster ovary" is already at :168 and matches first). It strips the phrase
    from any title, so "Syrian hamster ovary cells" — a primary hamster-tissue
    study, not CHO — is kept. Consistent with the documented precision-over-
    recall stance, but it silently converts an animal-derived tissue study into
    a keep with no boundary. · Fix: narrow to "chinese hamster ovary" (and its
    CHO expansion), or document the Syrian-hamster case as an accepted gap. ·
    confidence: low.

## Notes (not findings)

- The acronym rule (all-caps, length > 1) still keeps MICE/RATS/C. ELEGANS; the
  "rat-ios" hyphen case the previous re-gate probed is now DROPPED because "rat"
  is a bare term and `-` is a boundary — folded into F4.
- "Rodenticide poisoning" is correctly kept ("rodent" is bounded by the following
  "i", `(?!\w)` fails) — the boundary tightening in F4's fix must preserve this.
- "Cattle farmers" (human occupational-health) predates this fix ("cattle" was in
  the re-gate-1 list) and is out of scope for the F3 re-gate.

## VERDICT: REJECTED

F3 is answered on its named targets (25/25 animal titles drop, 19/19 human
titles keep, two new tests pin both directions), F1/F2 stay fixed, and the
suite is green (1608 passed, 2 skipped). The rejection is on F4 and F5: restoring
the bare singulars re-opens F1's silent false-exclusion class through hyphen
boundaries (anti-mouse/anti-rat/anti-rodent reagents, rat-bite fever, mouse-ear
cress), and the model-phrase list has a plural gap (bovine/ovine/canine/equine
models). Both fixes are cheap term-list/one-line edits; F6 (hamster ovary cells)
is a note-level cleanup that can ride along.
