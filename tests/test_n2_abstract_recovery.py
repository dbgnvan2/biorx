"""
N2 — web summaries did not recover a missing abstract.

Spec:  docs/implementation_plan_2026-09-16_backlog.md#N2
Found: live smoke test, 2026-09-16. "Interparental conflict and adolescent
online flaming" is open access with full text on PMC, but its search record had
no abstract and no DOI. The summary path only tried a PDF, so it failed with
"No abstract and no downloadable text".

The desktop app's AbstractFetchWorker had a recovery chain, with two defects
that would have failed the same paper there too: the PMC full-text step was
nested under `if doi`, so a PMCID-only paper never reached it; and a total
failure was emitted as the success value "(Abstract not available…)".

The chain now lives in src/paper_meta.recover_abstract(); the GUI worker and the
web summary route both call it. Network calls are stubbed, but the PMC response
is the real one for the paper that failed (tests/fixtures/), not a hand-written
JATS sample (learnings P19).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import paper_meta
from src.paper_meta import AbstractRecovery, pmcid_of, recover_abstract

FIXTURE = Path(__file__).parent / "fixtures" / "pmc13572605_fulltext.xml"

def _abstract(label: str) -> str:
    """A stub long enough to be an abstract (the recovery chain ignores anything
    under paper_meta.MIN_ABSTRACT_CHARS), tagged so a test can tell sources apart."""
    return (f"[{label}] We recruited 240 participants across two sites and "
            "measured stress responses over twelve weeks; results are reported below.")


# The record exactly as the Europe PMC adapter normalised it on 2026-09-16.
FLAMING_PAPER = {
    "title": "Interparental conflict and adolescent online flaming: the chain "
             "mediating roles of psychological stress and online disinhibition",
    "abstract": "", "doi": "", "pmcid": "PMC13572605",
    "best_oa_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13572605/",
    "source_url": "", "canonical_id": "pmcid:PMC13572605",
}


@pytest.fixture
def no_network():
    """Every source returns nothing unless a test says otherwise."""
    epmc = MagicMock()
    epmc.get_by_id.return_value = None
    epmc.fetch_abstract_from_fulltext.return_value = ""
    with patch("src.paper_meta._europepmc", return_value=epmc), \
         patch("src.paper_meta._crossref_abstract", return_value=""), \
         patch("src.paper_meta.fetch_openalex_abstract", return_value=""), \
         patch("src.paper_meta.scrape_abstract_from_url", return_value=""):
        yield epmc


# ── The paper that failed ─────────────────────────────────────────────────────

def test_n2_a_pmcid_only_paper_is_recovered_from_pmc_full_text(no_network):
    """The real paper, the real PMC response, parsed by the real JATS parser."""
    from src.sources.europepmc import _extract_abstract_from_jats_xml
    real_xml = FIXTURE.read_text()
    no_network.fetch_abstract_from_fulltext.side_effect = \
        lambda pmcid: _extract_abstract_from_jats_xml(real_xml) if pmcid == "PMC13572605" else ""

    result = recover_abstract(FLAMING_PAPER)

    assert result.found
    assert result.source == "pmc_fulltext"
    assert "online flaming" in result.text.lower()
    assert len(result.text) > 1000


def test_n2_pmc_is_tried_even_when_there_is_no_doi(no_network):
    """The original defect: the PMC step sat inside `if doi`."""
    recover_abstract({"pmcid": "PMC1", "doi": ""})
    no_network.fetch_abstract_from_fulltext.assert_called_once_with("PMC1")


@pytest.mark.parametrize("url,expected", [
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13572605/", "PMC13572605"),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/", "PMC123"),
    ("https://europepmc.org/article/PMC/PMC99", "PMC99"),
    ("https://doi.org/10.1/x", ""),
    ("", ""),
])
def test_n2_a_pmcid_is_read_from_an_open_access_link(url, expected):
    assert pmcid_of({"best_oa_url": url}) == expected


def test_n2_the_record_pmcid_wins_over_one_in_a_link():
    assert pmcid_of({"pmcid": "PMC1", "best_oa_url": ".../PMC2/"}) == "PMC1"


# ── The chain ─────────────────────────────────────────────────────────────────

def test_n2_a_doi_paper_tries_europe_pmc_first(no_network):
    no_network.get_by_id.return_value = {"abstractText": _abstract("europepmc")}
    result = recover_abstract({"doi": "10.1/x"})
    assert (result.text, result.source) == (_abstract("europepmc"), "europepmc")


def test_n2_the_pmcid_found_by_the_doi_lookup_is_used(no_network):
    no_network.get_by_id.return_value = {"abstractText": "", "pmcid": "PMC77"}
    no_network.fetch_abstract_from_fulltext.side_effect = \
        lambda p: _abstract("pmc") if p == "PMC77" else ""
    assert recover_abstract({"doi": "10.1/x"}).source == "pmc_fulltext"


def test_n2_falls_through_crossref_then_openalex_then_scrape(no_network):
    with patch("src.paper_meta._crossref_abstract", return_value=_abstract("crossref")):
        assert recover_abstract({"doi": "10.1/x"}).source == "crossref"
    with patch("src.paper_meta.fetch_openalex_abstract", return_value=_abstract("openalex")):
        assert recover_abstract({"doi": "10.1/x"}).source == "openalex"
    with patch("src.paper_meta.scrape_abstract_from_url", return_value=_abstract("scrape")):
        assert recover_abstract({"doi": "10.1/x"}).source == "scrape"


def test_n2_the_open_access_page_is_scraped_when_nothing_else_works(no_network):
    tried = []
    with patch("src.paper_meta.scrape_abstract_from_url",
               side_effect=lambda u: tried.append(u) or ""):
        recover_abstract({"best_oa_url": "https://oa.example/a", "source_url": "https://pub.example/a"})
    assert "https://oa.example/a" in tried and "https://pub.example/a" in tried


def test_n2_a_source_that_raises_does_not_stop_the_chain(no_network, caplog):
    """One flaky source must not hide an abstract another source has (P5)."""
    no_network.get_by_id.side_effect = ConnectionError("Europe PMC down")
    with patch("src.paper_meta.fetch_openalex_abstract", return_value=_abstract("openalex")), \
         caplog.at_level("INFO"):
        result = recover_abstract({"doi": "10.1/x"})
    assert result.source == "openalex"
    assert any("europepmc" in r.getMessage() and "failed" in r.getMessage()
               for r in caplog.records)


def test_n2_total_failure_is_not_a_string_that_looks_like_an_abstract(no_network):
    """
    The desktop worker emitted "(Abstract not available from any source)" as
    its success value — text that would be stored or summarized as an abstract
    (learnings P14).
    """
    result = recover_abstract({"doi": "10.1/x", "pmcid": "PMC1"})
    assert result.found is False
    assert result.text == ""
    assert result.source is None
    assert result.tried, "the result should say which sources were tried"


def test_n2_a_paper_with_nothing_to_look_up_makes_no_calls(no_network):
    result = recover_abstract({"title": "only a title"})
    assert not result.found
    no_network.get_by_id.assert_not_called()
    no_network.fetch_abstract_from_fulltext.assert_not_called()


# ── Both front ends use it (P25) ──────────────────────────────────────────────

def test_n2_gui_worker_and_web_route_use_the_same_recovery():
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui

    received = []
    worker = gui.AbstractFetchWorker(FLAMING_PAPER)
    worker.finished.connect(received.append)
    with patch("gui.recover_abstract",
               return_value=AbstractRecovery("Recovered.", "pmc_fulltext", ["pmc_fulltext"])) as rec:
        worker.run()
    rec.assert_called_once()
    assert rec.call_args.args[0]["pmcid"] == "PMC13572605"
    assert received == ["Recovered."]


def test_n2_gui_worker_shows_a_message_not_an_abstract_on_failure():
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui

    received = []
    worker = gui.AbstractFetchWorker({"doi": "10.1/x"})
    worker.finished.connect(received.append)
    with patch("gui.recover_abstract", return_value=AbstractRecovery("", None, ["europepmc"])):
        worker.run()
    assert received and received[0].startswith("(") and "not available" in received[0]


# ── The notice list is configuration, not code ────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Correction to: Exploring the association between stress and skin", "correction to"),
    ("ERRATUM: Figure 2 mislabelled", "erratum"),
    ("Retraction Note: A study of X", "retraction"),
    ("Corrections in adolescent sleep data", ""),        # an article, not a notice
    ("A study of retraction rates", ""),                  # prefix only, not contains
    ("", ""),
])
def test_n2_notice_titles_are_recognised_from_the_shipped_config(title, expected):
    from src.llm_config import load_llm_config, non_article_kind
    assert non_article_kind(load_llm_config(), title) == expected


def test_n2_the_notice_list_is_read_from_config_not_hardcoded():
    from src.llm_config import non_article_kind
    custom = {"not_summarizable_title_prefixes": ["addendum"]}
    assert non_article_kind(custom, "Addendum: new data") == "addendum"
    assert non_article_kind(custom, "Correction to: x") == ""


# ── The desktop dialog asks for recovery whenever there is something to look up ─

@pytest.mark.parametrize("paper,should_fetch", [
    ({"title": "t", "abstract": "", "pmcid": "PMC13572605"}, True),
    ({"title": "t", "abstract": "", "best_oa_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/"}, True),
    ({"title": "t", "abstract": "", "source_url": "https://pub.example/a"}, True),
    ({"title": "t", "abstract": "", "doi": "10.1/x"}, True),
    ({"title": "t", "abstract": "Already here."}, False),
    ({"title": "t", "abstract": ""}, False),
])
def test_n2_detail_dialog_fetches_whenever_there_is_something_to_look_up(paper, should_fetch):
    """
    The dialog used to ask only when a paper had a DOI or a PMCID, so a paper
    with just an open-access or landing-page link never got a recovery attempt.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    import gui

    app = QApplication.instance() or QApplication(sys.argv[:1])
    with patch.object(gui.PaperDetailDialog, "_fetch_abstract") as fetch:
        dialog = gui.PaperDetailDialog(paper)
    assert fetch.called is should_fetch
    dialog.deleteLater()


# ── From the N2 gate ──────────────────────────────────────────────────────────

def test_n2_both_front_ends_import_the_one_recovery_function():
    """
    Identity, not equivalence (gate F2): a copy in either front end would drift,
    exactly as the filter logic did before it was shared.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    from web import routes_summaries
    assert gui.recover_abstract is paper_meta.recover_abstract
    assert routes_summaries.recover_abstract is paper_meta.recover_abstract


def test_n2_the_web_route_imports_the_one_recovery_function():
    """The same identity check where PyQt6 is not installed."""
    from web import routes_summaries
    assert routes_summaries.recover_abstract is paper_meta.recover_abstract


@pytest.mark.parametrize("junk", ["Error", "Not available.", "Abstract", "N/A", "See full text."])
def test_n2_a_too_short_result_is_not_accepted_as_an_abstract(no_network, junk):
    """Gate F3: every source, not only the scraper, is held to the length floor."""
    no_network.get_by_id.return_value = {"abstractText": junk}
    with patch("src.paper_meta.fetch_openalex_abstract",
               return_value="A real abstract long enough to count, describing methods, "
                            "participants and the main result in a full sentence."):
        result = recover_abstract({"doi": "10.1/x"})
    assert result.source == "openalex", f"{junk!r} was accepted as an abstract"


def test_n2_a_programming_error_in_a_source_is_logged_loudly(no_network, caplog):
    """
    Per-source isolation exists for flaky services. It must not turn a bug in our
    own code into a quiet "source failed" at INFO — which is how a NameError in
    these very tests went unnoticed while they were being written.
    """
    no_network.get_by_id.side_effect = NameError("name 'helper' is not defined")
    with caplog.at_level("INFO"):
        recover_abstract({"doi": "10.1/x"})
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, "a NameError in a source was logged below ERROR"
    assert errors[0].exc_info is not None, "no traceback for a programming error"


def test_n2_a_network_failure_in_a_source_stays_quiet(no_network, caplog):
    no_network.get_by_id.side_effect = ConnectionError("Europe PMC down")
    with caplog.at_level("INFO"):
        recover_abstract({"doi": "10.1/x"})
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
