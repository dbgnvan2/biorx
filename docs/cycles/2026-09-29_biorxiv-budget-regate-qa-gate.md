# Re-gate — bioRxiv/medRxiv match budget + species title terms (2026-09-29)

Second learning-qa sweep over `origin/main...HEAD`. The first gate
(`2026-09-29_biorxiv-budget-qa-gate.md`) REJECTED f854662 on F1 (species title
terms dropping human studies) and F2 (progress channel mixing papers read with
matches). 7acd226 answers both; 167045e advances the W1.a drift baseline. This
gate confirms the two fixes, re-probes the new term list adversarially, checks
for regressions, and runs the suite. Fix commits were re-swept as their own
range (2162447..HEAD) per P26.

RANGE:       origin/main...HEAD  (merge-base diff; origin/main = 63912ab, HEAD = 167045e)
COMMITS:     4 — f854662 feat (budget + page limit), 2162447 test(drift) baseline,
                   7acd226 fix (F1 term list + F2 progress channel),
                   167045e test(drift) baseline advance
             (re-swept fix range: 2162447..HEAD = 7acd226 + 167045e)
APPLICABLE:  P5, P7, P12, P13, P19, P26, P27, P29, P36
CHECKED:     P1 P2 P3 P4 P5 P7 P9 P12 P13 P19 P21 P26 P27 P28 P29 P36
NOT COVERED: security beyond the XSS sink (P8 SSRF untouched), concurrency,
             performance, logic-correctness beyond the reviewed paths, authN/authZ.
SUITE:       venv/bin/python -m pytest tests/ -q  →  1606 passed, 2 skipped (green).

## Fix 1 — species title terms (F1) — CONFIRMED FIXED

The term list is now plurals and model phrases only: `mice`, `rats`, `rodents`,
`zebrafish`, `drosophila`, `caenorhabditis`, `c. elegans`, `macaques`,
`non-human primates`, `nonhuman primates`, `piglets`, `hamsters`, `cattle`, plus
`mouse/rat/murine/rodent model(s)`. Single organism words (`mouse`, `rat`,
`bovine`, `porcine`, `rabbit`, `hamster`, `canine`, `equine`, `murine`, …) are
gone. `title_names_an_animal_study()` treats a match that is all-caps with
length > 1 as an acronym, so `MICE` (multiple imputation by chained equations)
is not matched.

Adversarial probe (filter_papers with species=no-animal):

- MUST KEEP — 20/20 human studies kept, including the gate's eight titles
  (bovine serum albumin, porcine bioprosthetic valve, rabbit monoclonal
  antibody, Chinese hamster ovary cells, canine tooth, MICE, murine typhus,
  equine-assisted therapy) plus 12 more (ovine collagen, canine-assisted
  psychotherapy, RATS acronym, feline leukemia screening, broiler-farm workers,
  rat-ios, murine monoclonal antibody, etc.).
- MUST DROP — 16/16 animal studies dropped (plurals and model phrases):
  "… in mice", "murine model of brain injury", "zebrafish larvae",
  "non-human primates", "… in Rats", "mouse model of Alzheimer's", "rat model
  of hypertension", "rodents", "macaques", "C. elegans", "Drosophila",
  "Caenorhabditis", "piglets", "hamsters", "cattle", "nonhuman primates".
- Raw matcher sanity: mice→True, MICE→False, Mice→True, rats→True, RATS→False,
  mouse model→True, MOUSE MODEL→False, murine model→True, C. elegans→True.

Verdict on F1: the false-exclusion defect is gone. All eight gate titles and the
12 additional human-study probes survive; the acronym rule handles MICE and RATS.
F1 is answered.

## Fix 2 — progress channel (F2) — CONFIRMED FIXED

`_search_source` now branches on `local_filter`: a locally-filtered source
reports `on_progress(fetched, 0)` (matches, no known total) and papers read via
`on_status` ("N of M papers read, K match so far…"); a non-local source keeps
`on_progress(fetched, src_total)`. The cumulative wrapper (`on_source_progress`
in `search()`) now sums matches uniformly across sources, so the bar's
"fetched" no longer inflates ~17x during the bioRxiv phase. The web consumer
sets `job.phase = message`, so the per-page status lines are a live phase update
(last wins), not accumulated clutter.

The new test `test_br3_progress_counts_matches_and_status_shows_papers_read`
asserts `max(f for f, _t in progress) == 6` (exact — 300 papers read, 6 match)
and the status string "300 of 300 papers read, 6 match so far…". Reverting to
the old `on_progress(seen_raw, …)` would make that `300`, so the test is
mutation-proof (P27/P29). F2 is answered.

## FINDINGS (ranked)

F3 · P26 (fix-commit over-correction; mirror of the gate's P7) ·
    filter_vocabulary.yaml `animal_title_terms` + src/filter_vocabulary.py ·
    The F1 fix dropped single organism words the gate never implicated as
    false-positive sources. "mouse", "rat", "rodent", "macaque" and singular
    "hamster" have no reagent/device/cell-line/anatomy/disease sense (the only
    thin human-sense collision is "mouse tracking" in human decision-making
    research, and only for "mouse"). F1's examples — bovine, porcine, rabbit,
    hamster(CHO), canine, equine, murine, MICE — are exactly the words with such
    senses; F1's recommended fix ("drop those seven") would have kept
    mouse/rat/rodent/macaque. Removing them guts the title check, which is the
    ONLY species mechanism for bioRxiv/medRxiv, arXiv, PsyArXiv, SocArXiv and
    OSF. Probe: 13/13 titles with a singular organism are now KEPT (should be
    dropped) — "Mouse embryonic stem cell differentiation", "Mouse brain
    development and plasticity", "Rat liver regeneration", "Hamster model of
    diet-induced obesity", "Macaque visual cortex recordings", "Rabbit aortic
    ring angiogenesis assay", "Bovine mastitis", "Porcine pancreatic islet
    transplantation", "Ovine pulmonary artery endothelial cells", "Canine
    osteoarthritis progression", "Equine laminitis", "Broiler chicken growth",
    "Murine leukemia virus pathogenesis". The CHANGELOG/YAML claim "missing an
    occasional animal study" misstates the gap: "mouse"/"rat" singular are the
    two most common lab-animal title words, not an edge case. A "Human studies
    only" search on bioRxiv will still surface a large fraction of mouse/rat
    studies. · Fix (cheap): restore "rat", "rodent", "macaque", "hamster"
    singulars (no collision), and add "X model" phrases for the dropped
    organisms ("rabbit model", "hamster model", "porcine model", "bovine
    model", "canine model", "equine model", "ovine model"; "murine model" is
    already present); for "mouse", restore it or keep "mice"+"mouse model" but
    state the mouse-tracking collision explicitly. · confidence: high ·
    severity: medium (papers are visible, not silently lost; the feature is
    additive over origin/main).

## Notes (not findings)

- Europe PMC/PubMed species exclusion is unchanged and still works at query
  time: `query_builder._species_clause` → `excluded_organisms` ("Mouse", "Rat",
  "Mus musculus", …) emits `NOT Mouse NOT Rat …`. The F3 recall gap therefore
  affects only sources whose sole species mechanism is the title check, not
  Europe PMC/PubMed. The title check is a NEW feature — origin/main had no
  client-side species filter at all — so nothing regressed relative to the
  merge base.
- Status wording is singular ("6 match so far…", "6 match the filter"); cosmetic.
- An all-caps title ("EFFECTS OF X IN MICE") is treated as acronym and kept;
  conservative and consistent with the documented precision-over-recall stance.
- The W1.a baseline advance (167045e) is the documented exception process;
  test-only, asserts the new on_progress/on_status wiring.

## VERDICT: REJECTED

F1 (false exclusion of human studies) and F2 (progress channel mixing) are both
correctly fixed and verified — 20/20 human titles kept, 16/16 animal plurals and
model phrases dropped, progress now counts matches with an exact-value test, and
the suite is green (1606 passed, 2 skipped). The rejection is narrow and on F3
alone: the fix over-corrected by removing "mouse"/"rat"/"rodent"/"macaque"/
"hamster" singulars that F1 never implicated, so the new title-based exclusion
misses the modal animal-study titles, and the docs' "occasional" claim understates
a modal gap. The fix is a cheap term-list edit (restore the collision-free
singulars and add model phrases for the dropped organisms).
