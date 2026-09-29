"""
Plain text from source markup (src/sources/markup.py).

Spec: docs/cycles/2026-09-29_browser-run.md — Europe PMC abstracts showed raw
"<h4>Objective</h4>" in the page.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sources.markup import markup_to_text


def test_br2_headings_become_labels():
    got = markup_to_text("<h4>Objective</h4>Age-related loss.<h4>Methods</h4>Sixty-six participants.")
    assert got == "Objective: Age-related loss.\nMethods: Sixty-six participants."


def test_br2_inline_tags_go_and_entities_are_decoded():
    assert markup_to_text("T<sub>reg</sub> cells &amp; IL-1&beta;") == "Treg cells & IL-1β"


def test_br2_less_than_and_greater_than_in_text_survive():
    """Adversarial: comparison signs look like tag edges. A blanket <[^>]+>
    removal turned this into "Survival was higher (p  1)"."""
    text = "Survival was higher (p < 0.05) when dose > 1 mg and age<60."
    assert markup_to_text(text) == text
    assert markup_to_text("<p>Survival (p < 0.05, dose > 1 mg)</p>") == "Survival (p < 0.05, dose > 1 mg)"


def test_br2_jats_paragraphs():
    assert markup_to_text("<jats:p>Background.</jats:p><jats:p>Methods.</jats:p>") == \
        "Background.\n\nMethods."


def test_br2_real_europe_pmc_record_reads_as_text():
    """The fixture recorded from the real API carries <p> in abstractText."""
    from src.sources.europepmc import EuropePmcAdapter
    raw = json.loads((Path(__file__).parent / "fixtures/europepmc_core_sample.json").read_text())
    rows = raw if isinstance(raw, list) else raw.get("resultList", {}).get("result", raw)
    tagged = [r for r in rows if "<" in (r.get("abstractText") or "")]
    assert tagged, "the fixture no longer has a tagged abstract"
    for r in tagged:
        rec = EuropePmcAdapter().normalize(r)
        assert "<p>" not in rec.abstract and "</p>" not in rec.abstract
        assert rec.abstract


def test_br2_crossref_uses_the_same_rules():
    from src.sources.crossref import _strip_jats
    assert _strip_jats("<jats:p>Effect (p < 0.01) was large.</jats:p>") == "Effect (p < 0.01) was large."


def test_br2_a_letter_after_less_than_is_not_a_tag():
    """QA gate 2026-09-29: "<b and c>" was read as a <b> tag with attributes."""
    for text in ("a<b and c>d", "x<a and y>z", "u<i and j>v"):
        assert markup_to_text(text) == text
    assert markup_to_text("<i>E. coli</i> and <b>bold</b> <a href='x'>link</a>") == "E. coli and bold link"
