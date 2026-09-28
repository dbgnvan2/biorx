"""
Tests for the filter vocabulary (filter_vocabulary.yaml + src/filter_vocabulary.py).

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#S3, #B5
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import filter_vocabulary as vocab
from src.filtering import filter_papers, normalise_filter

ROOT = Path(__file__).parent.parent


# ── S3: one vocabulary, ids stored, legacy labels still read ─────────────────

def test_s3_legacy_labels_normalise_to_ids():
    for facet in vocab.FACETS:
        ids = vocab.option_ids(facet)
        assert "any" in ids, facet
        for legacy, mapped in (vocab.load()[facet].get("legacy") or {}).items():
            got = vocab.normalise_value(facet, legacy)
            assert got == mapped and (got is None or got in ids), (facet, legacy)


def test_s3_every_saved_filter_normalises_without_unknown_values(caplog):
    """The real filters.json (and so every seeded web account) still reads."""
    data = json.loads((ROOT / "filters.json").read_text())
    filters = data.get("filters", data) if isinstance(data, dict) else data
    for f in filters:
        n = normalise_filter(f)
        for facet in vocab.FACETS:
            if facet in n:
                assert n[facet] in vocab.option_ids(facet), (f.get("name"), facet, n[facet])
    assert "ignoring" not in caplog.text


def test_s3_saved_filter_meaning_is_unchanged():
    """Adversarial: a relabelled option must keep matching what it matched."""
    n = normalise_filter({"species": "Exclude animal studies",
                          "paper_type": "review article", "version": "2+ (revised only)"})
    assert (n["species"], n["paper_type"], n["version"]) == ("no-animal", "review", "revised")


def test_s3_unknown_value_ignored_with_warning(caplog):
    n = normalise_filter({"name": "X", "paper_type": "editorial"})
    assert n["paper_type"] == "any"
    assert "editorial" in caplog.text


def test_s3_vocab_has_no_literals_in_code():
    """Rule 9: option labels and the organism list live in the YAML only.
    A source scan is the right check here: the defect is where text lives."""
    literals = ["review article", "Human studies only", "Exclude animal studies",
                "Animal studies only", "2+ (revised only)", "1 (first submission only)",
                "preprints only (not in journal)", "published in journal only",
                "Mus musculus", "Drosophila", '"cc_by"', "'cc_by'"]
    for rel in ("src/filtering.py", "src/sources/query_builder.py"):
        text = (ROOT / rel).read_text()
        found = [lit for lit in literals if lit in text]
        assert not found, (rel, found)


def test_s3_client_options_have_ids_and_labels():
    opts = vocab.for_client()
    assert set(opts) == set(vocab.FACETS)
    for facet, items in opts.items():
        assert items[0] == {"id": "any", "label": "(any)"}, facet
        assert len({i["id"] for i in items}) == len(items), facet


# ── B5: every option can match something a source produces ───────────────────

# (adapter, raw record) pairs shaped like each source's real responses.
def _records():
    from src.sources.europepmc import EuropePmcAdapter
    from src.sources.biorxiv_medrxiv import BiorxivMedrxivAdapter
    from src.sources.psyarxiv import PsyArxivAdapter
    from src.sources.arxiv import ArxivAdapter

    epmc = EuropePmcAdapter()
    out = []
    for pub_type, lic in [("journal article", "cc by"), ("review", "cc by-nc-sa"),
                          ("clinical trial", "cc by-sa"), ("preprint", "cc by-nc-nd")]:
        out.append(epmc.normalize({"doi": f"10.1/{pub_type}", "title": pub_type,
                                   "pubType": pub_type, "license": lic,
                                   "journalTitle": "J"}))
    bx = BiorxivMedrxivAdapter.__new__(BiorxivMedrxivAdapter)
    for lic, ver in [("cc_by_nc", "2"), ("cc_by_nd", "1"), ("cc_no", "1"), ("cc0", "3")]:
        out.append(bx.normalize({"doi": f"10.1101/{lic}", "title": lic, "version": ver,
                                 "license": lic, "server": "biorxiv"}))
    osf = PsyArxivAdapter()
    out.append(osf.normalize({"id": "o1", "attributes": {
        "title": "osf", "license": {"name": "CC0 1.0 Universal"}}}))
    arx = ArxivAdapter.__new__(ArxivAdapter)
    out.append(arx.normalize({"arxiv_id_full": "2401.00001v2", "title": "arxiv"}))
    # Unpaywall enrichment writes its own licence spelling onto a record.
    enriched = epmc.normalize({"doi": "10.1/pd", "title": "pd", "pubType": "journal article"})
    enriched.license = "public-domain"
    out.append(enriched)
    return [r.to_dict() for r in out]


@pytest.mark.parametrize("facet", ["paper_type", "version", "published", "license"])
def test_b5_every_facet_option_matchable_by_some_adapter(facet):
    papers = _records()
    for opt in vocab.option_ids(facet):
        if opt == "any":
            continue
        assert filter_papers(papers, {facet: opt}), f"{facet}={opt} matches nothing"


@pytest.mark.parametrize("raw,expected", [
    ("cc_by", "cc-by"),                                   # bioRxiv
    ("cc_by_nc_nd", "cc-by-nc-nd"),
    ("cc by-nc", "cc-by-nc"),                             # Europe PMC
    ("CC BY 4.0", "cc-by"),
    ("cc-by-sa", "cc-by-sa"),                             # Unpaywall
    ("public-domain", "pd"),
    ("CC-By Attribution 4.0 International", "cc-by"),     # OSF
    # Adversarial: OSF's long form must not read as plain CC BY.
    ("CC-By Attribution-NonCommercial-NoDerivatives 4.0", "cc-by-nc-nd"),
    ("CC-By Attribution-ShareAlike 4.0 International", "cc-by-sa"),
    ("CC0 1.0 Universal", "cc0"),
    ("https://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc"),
    ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0"),
    ("No license", "none"),
    ("cc_no", "none"),
    ("implied-oa", ""),
    ("", ""),
])
def test_b5_license_forms_normalise(raw, expected):
    assert vocab.license_id(raw) == expected


def test_b5_cc_by_does_not_match_cc_by_nc():
    """Adversarial: the old substring test let "cc_by" match "cc_by_nc"."""
    nc = {"title": "t", "abstract": "", "license": "cc_by_nc"}
    assert filter_papers([nc], {"license": "cc-by"}) == []
    assert filter_papers([nc], {"license": "cc_by_nc"}) == [nc]


def test_b5_review_article_filter_matches_reviews():
    """Live: "Loneliness" (paper_type "review article") matched 0 of 248."""
    review = {"title": "t", "abstract": "", "type": "review"}
    article = {"title": "t", "abstract": "", "type": "article"}
    assert filter_papers([review, article], {"paper_type": "review article"}) == [review]


def test_b5_stored_institution_is_ignored(caplog):
    n = normalise_filter({"name": "Old", "institution": ["Harvard"]})
    assert n["institution"] == ""
    assert "institution" in caplog.text


def test_b5_institution_refused_by_problems():
    assert vocab.problems({"institution": "Harvard"})
    assert vocab.problems({"institution": ""}) == []
