# Re-gate 4 — bioRxiv/medRxiv species title terms (2026-09-29)

Fifth learning-qa sweep over `origin/main...HEAD`. Re-gate 3 REJECTED on F8
(the left-hyphen boundary of f52b58e kept "knockout-mouse model" — a measured
regression) and F7 (the right-hyphen side still dropped legitimate reagents
like "mouse-human chimeric antibody"). Commit 6c728a0 answers both: the
left-hyphen boundary is gone (plain `(?<!\w)` again), a reagent prefix
(`species.animal_title_reagent_prefixes: [anti]`) is matched as an exception,
and the exception phrases now join their words with a hyphen-or-space, with the
six F7 reagent preparations added. This gate confirms F8 and F7 are fixed,
probes both directions adversarially (spelling variants: hyphen vs space vs
none; and the mirror word order of the chimeric reagent), confirms F1–F6 stay
fixed, and runs the suite.

RANGE:       origin/main...HEAD  (origin/main = 63912ab, HEAD = 6c728a0)
             fix re-swept as its own range: f52b58e..HEAD = 6c728a0 (per P26).
COMMITS:     7 — f854662 feat (budget + page limit), 2162447 test(drift) baseline,
                   7acd226 fix (F1 term list + F2 progress channel),
                   167045e test(drift) baseline advance,
                   108a6c7 fix(species): restore lab-animal words + exceptions (F3),
                   f52b58e fix(species): hyphen compounds, plural models, CHO-only (F4–F6),
                   6c728a0 fix(species): reagent prefixes in config, not a hyphen boundary (F7, F8)
APPLICABLE:  P3, P4, P5, P7, P10, P26, P27
CHECKED:     P1 P2 P3 P4 P5 P6 P7 P10 P13 P19 P26 P27 (matcher, exception strip,
             reagent-prefix branch, filter_papers caller, the two reworked tests,
             the fix range re-swept, and a differential probe old-vs-new across
             108a6c7 → f52b58e → 6c728a0 for the three hyphen orientations).
NOT COVERED: security beyond the XSS sink (P8 SSRF untouched), concurrency,
             performance, authN/authZ; Europe PMC/PubMed query-time species
             clause (unchanged, outside this diff).
SUITE:       venv/bin/python -m pytest tests/ -q  →  1610 passed, 2 skipped (green).

## F8 / F7 answered on their named targets — CONFIRMED

F8 (knockout/transgenic leak): the boundary is plain `(?<!\w)` again, so a
hyphen before the animal word is no longer read as "not the animal". Probe via
`filter_papers(..., {"species": "no-animal"})`:

- Must DROP (7/7): "A knockout-mouse model of colitis", "Transgenic-mouse
  studies of amyloid", "SCID-mouse xenograft growth", "Nude-mouse xenograft of
  melanoma", "Wild-type-mouse controls for gut microbiota",
  "Germ-free-mouse colonization", "A knockout-rat model of hypertension".
- Spelling variants still DROP (8/9): the open compounds ("knockout mouse",
  "SCID mouse", "wild type mouse", "germ free mouse", "knockout rat") all drop.
  The one exception is the fused spelling "knockoutmouse", which keeps — but
  that is not a word anyone writes; the animal term is only matched as a whole
  word, so a fused compound is correctly out of scope (see Notes).

Differential probe (pitfall 3, `git show <commit>:` exec'd):
"knockout-mouse model" DROP at 108a6c7 → KEEP at f52b58e (the F8 regression) →
DROP at 6c728a0. The regression is gone.

F7 (right-hyphen reagents): six reagent/preparation phrases added, and the
exception join is `[\s-]+`, so a hyphen and a space count the same. Must KEEP
(6/6): "mouse-human chimeric antibody", "mouse-mouse hybridoma", "rat-tail
collagen", "rat liver microsomes", "rat-brain homogenate", "mouse-skin
extract", each also passing in the space spelling ("mouse human chimeric",
"rat tail collagen", …). The mirror "human-mouse xenograft" still DROPS
(correctly — a xenograft is an animal study).

## F1 / F2 / F3 / F4 / F5 / F6 stay fixed — CONFIRMED

- F1 (false exclusion of human studies): all eight gate titles (bovine serum
  albumin, porcine valve, rabbit monoclonal antibody, CHO, canine tooth, MICE,
  murine typhus, equine-assisted therapy) plus the re-gate keeps (mouse
  tracking, computer mouse, ratio, dog ownership, stress/strain,
  pirates/parrots, mousetrap-shaped) KEEP — 15/15.
- F2 (progress channel): untouched by 6c728a0 (edits only
  filter_vocabulary.{py,yaml}, tests/test_filtering.py, docs); green suite.
- F3 (bare singulars + model phrases): mouse/rat/hamster/macaque/rodent and
  the porcine/rabbit model phrases DROP — 8/8.
- F4 (hyphen compounds on the left = reagent): "anti-mouse antibody panel",
  "anti-rat IgG", "anti-rodent antibody", plus "anti mouse" (space) and
  "antimouse" (none) all KEEP via the reagent-prefix rule — 6/6.
- F5 (plural models): bovine/ovine/canine/equine models DROP — 4/4.
- F6 (CHO-only): "Chinese hamster ovary cells" KEEPS, "Syrian hamster ovary
  cells after infection" DROPS — 2/2.

## FINDINGS (ranked)

F9 · P26/P7 (fix commit leaves the class half-closed; false positive) ·
    filter_vocabulary.yaml:183-188 + src/filter_vocabulary.py:214-223 ·
    The chimeric-antibody exception was added in one word order only. The
    mirror orientation still DROPS under "Exclude animal studies":
    "A human-mouse chimeric antibody in lymphoma patients",
    "Human mouse chimeric antibody therapy" (both hyphen and space spellings)
    — a human/therapeutic study wrongly excluded. The animal word here sits
    RIGHT of the hyphen and is preceded by a real prefix ("human-"), which the
    reagent-prefix rule does not cover (it only knows "anti"), and the
    exception list has "mouse human chimeric" but not "human mouse chimeric".
    F7's own fix suggestion warned against exactly this: "share one rule for
    both orientations so neither side drifts" — the drift happened, and the
    re-gate 3 verdict required "must-keep tests on each side".
    Differential probe: DROP at 108a6c7 → KEEP at f52b58e (via the blanket
    left-hyphen boundary, an accident) → DROP at 6c728a0. So it is not a
    regression against the pre-sweep baseline (108a6c7 dropped it too), but it
    is a clear failure of the stated symmetric-closure rule and a precision
    failure of the documented stance — "not dropping human studies" is the one
    thing the vocabulary comment says the design exists to protect. ·
    Fix (cheap): add "human mouse chimeric" to `animal_title_exceptions` (the
    `[\s-]+` join already covers both spellings), and pin it with a must-keep
    test next to the "mouse human chimeric" case. · confidence: high ·
    severity: medium (false positive, the wrong side of the precision trade;
    narrower than F8's knockout-mouse class but the same defect family).

## Notes (not findings)

- "knockoutmouse" (fused, no separator) KEEPs. This is the correct reading of
  the whole-word rule ("mouse" inside "knockoutmouse" is not a word); no
  author writes it, so it is not a defect.
- "non-mouse" / "non-rat" (a negation prefix the F8 suggestion mentioned
  alongside "anti-") still DROP. Not flagged: "non-mouse" usually means
  "another animal" (rat/rabbit), so dropping it is right more often than not,
  and it was never an adopted rule — "anti" is the only prefix configured.
- "rat liver microsomal metabolism" (adjective, vs the listed noun
  "microsomes") still DROP. Same literal-list limitation as F9's root cause;
  not a separate finding.
- The exception phrases are each scoped by their full text ("rat liver
  microsomes" does not strip "rat liver regeneration"), so the broader
  `[\s-]+` join does not open any new false-negative I could find.
- The two reworked tests are provable-failing: `test_br7_hyphen_compounds_…`
  asserts an exact keep-list equality; `test_br7_hyphenated_animal_titles_…`
  asserts `== []`. Both went red in a mutation check by inspection and are
  green in the suite.

## VERDICT: REJECTED

F8 and F7 are answered on their named targets (7/7 hyphenated animal titles
drop, 6/6 reagent preparations keep), F1–F6 stay fixed, and the suite is green
(1610 passed, 2 skipped). The rejection is on F9, the same half-closed class
that drove re-gate 3's rejection: the chimeric-reagent exception was added in
one word order only, so its mirror ("human-mouse chimeric antibody") — a
human/therapeutic study — is still wrongly dropped. It is a false positive on
the wrong side of the documented precision-over-recall stance, and it is the
exact "one orientation, not both" drift F7's fix suggestion said to avoid. The
fix is one exception phrase plus a must-keep test.
