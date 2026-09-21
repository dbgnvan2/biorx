"""
Tests for M4 — one synthesis across several papers.

Spec: docs/implementation_plan_2026-09-20_references_batch.md#M4
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import review


def _item(paper_id, title, abstract="", **extra):
    return {"item_id": paper_id, "paper": dict(
        {"paper_id": paper_id, "title": title, "abstract": abstract,
         "authors": "Author A", "pub_date": "2026-01-01"}, **extra)}


def _summary(findings, source_text=review.FULL_TEXT, **extra):
    return dict({"key_findings": findings, "methodology": "Methods here.",
                 "conclusions": "Conclusions here.", "source_text": source_text},
                **extra)


# ── M4.A.1: what the synthesis reads, and what it admits to reading ───────────

def test_m4a1_a_summary_is_preferred_over_the_abstract():
    items = [_item(1, "Paper one", abstract="The abstract.")]
    got = review.gather(items, {1: _summary(["A finding"])})
    assert len(got["contributors"]) == 1
    contributor = got["contributors"][0]
    assert contributor["basis"] == review.FULL_TEXT
    assert "A finding" in contributor["text"]
    assert "The abstract." not in contributor["text"]


def test_m4a1_the_abstract_is_used_when_there_is_no_summary():
    items = [_item(1, "Paper one", abstract="The abstract.")]
    got = review.gather(items, {})
    assert got["contributors"][0]["basis"] == review.ABSTRACT
    assert got["contributors"][0]["text"] == "The abstract."


def test_m4a1_a_kept_abstract_is_not_a_full_text_summary():
    """The decisive distinction. A summary row whose source_text is "abstract"
    is the abstract standing in because no full text was found — the model
    never read the paper. Counting it as full text would make the synthesis
    look better grounded than it is."""
    items = [_item(1, "Paper one", abstract="The abstract.")]
    stored = _summary(["Not really a finding"], source_text=review.ABSTRACT)
    got = review.gather(items, {1: stored})
    assert got["contributors"][0]["basis"] == review.ABSTRACT


def test_m4a2_a_paper_with_neither_is_excluded_and_reported():
    """P2: not silently dropped. A synthesis over four of six papers that says
    it covered six is a misleading document."""
    items = [_item(1, "Has an abstract", abstract="Text."),
             _item(2, "Has nothing at all")]
    got = review.gather(items, {})
    assert len(got["contributors"]) == 1
    assert len(got["excluded"]) == 1
    assert got["excluded"][0]["title"] == "Has nothing at all"
    assert "no summary and no abstract" in got["excluded"][0]["reason"]


def test_m4a1_an_empty_summary_falls_through_to_the_abstract():
    """A stored summary with no content in it is not usable material."""
    items = [_item(1, "Paper one", abstract="The abstract.")]
    blank = {"key_findings": [], "methodology": "", "conclusions": "",
             "source_text": review.FULL_TEXT}
    got = review.gather(items, {1: blank})
    assert got["contributors"][0]["basis"] == review.ABSTRACT
    assert got["contributors"][0]["text"] == "The abstract."


# ── M4.A.1: the prompt says what each paper was read from ─────────────────────

def test_m4a1_the_prompt_labels_each_paper_with_its_basis():
    """The model must know which papers it is reading second-hand, or it will
    weigh an abstract like a full paper."""
    items = [_item(1, "Full paper", abstract="a"), _item(2, "Abstract paper", abstract="b")]
    gathered = review.gather(items, {1: _summary(["Finding"])})
    built = review.build_prompt(gathered)
    assert "(read from: full text)" in built["prompt"]
    assert "(read from: abstract only)" in built["prompt"]
    assert "[1] Full paper" in built["prompt"] and "[2] Abstract paper" in built["prompt"]


def test_m4a1_the_system_prompt_forbids_inventing_findings():
    """L5/L4: the instruction layer must tell the model not to supply what the
    papers do not."""
    assert "Do not invent" in review.SYSTEM_PROMPT
    assert "say so" in review.SYSTEM_PROMPT


def test_m4a1_result_records_which_papers_contributed():
    items = [_item(1, "One", abstract="a"), _item(2, "Two", abstract="b")]
    built = review.build_prompt(review.gather(items, {}))
    assert [c["title"] for c in built["included"]] == ["One", "Two"]
    assert all("basis" in c for c in built["included"])


# ── M4.A.4: the cap, and what it drops ────────────────────────────────────────

def test_m4a4_oversized_selection_is_capped_and_says_so():
    """P9, at real scale: a big list must not be silently truncated. The cap is
    sized here so it actually bites."""
    items = [_item(n, f"Paper {n}", abstract="x" * 2000) for n in range(1, 21)]
    built = review.build_prompt(review.gather(items, {}), max_chars=9000)
    assert built["included"], "the cap must not drop everything"
    assert built["dropped"], "the cap must bite on 20 papers of 2000 chars"
    assert len(built["included"]) + len(built["dropped"]) == 20
    assert len(built["prompt"]) <= 9000 + 2500     # one block's slack
    assert all("too long" in d["reason"] for d in built["dropped"])


def test_m4a4_the_cap_keeps_the_first_papers_not_an_arbitrary_set():
    """The order the user sees is the order that survives, so the review covers
    a comprehensible subset rather than a random one."""
    items = [_item(n, f"Paper {n}", abstract="x" * 2000) for n in range(1, 11)]
    built = review.build_prompt(review.gather(items, {}), max_chars=7000)
    kept = [c["title"] for c in built["included"]]
    assert kept == [f"Paper {n}" for n in range(1, len(kept) + 1)]


def test_m4a4_one_huge_paper_is_still_sent_rather_than_everything_dropped():
    """A single paper larger than the cap must produce a review of that paper,
    not an empty prompt."""
    items = [_item(1, "Enormous", abstract="x" * 50000)]
    built = review.build_prompt(review.gather(items, {}), max_chars=1000)
    assert len(built["included"]) == 1
    assert not built["dropped"]


def test_m4a1_nothing_to_review_produces_no_prompt():
    """No prompt means no model call: a review of nothing would be invention."""
    built = review.build_prompt(review.gather([_item(1, "Empty")], {}))
    assert built["prompt"] == ""
    assert built["included"] == []
    assert len(built["excluded"]) == 1


# ── The basis note the reader sees ────────────────────────────────────────────

@pytest.mark.parametrize("bases,expected", [
    ([review.FULL_TEXT] * 3, "Based on 3 full-text summaries."),
    # Singular, in every branch. I first wrote this expecting "1 full-text
    # summaries" — matching the output rather than the intent is how a wrong
    # string gets frozen into a test.
    ([review.FULL_TEXT], "Based on 1 full-text summary."),
    ([review.ABSTRACT] * 4,
     "Based on 4 abstracts only — no full text was available for any of these "
     "papers."),
    ([review.ABSTRACT],
     "Based on 1 abstract only — no full text was available for that paper."),
    ([review.FULL_TEXT, review.ABSTRACT, review.ABSTRACT],
     "Based on 1 full-text summary and 2 abstracts."),
    ([review.FULL_TEXT, review.FULL_TEXT, review.ABSTRACT],
     "Based on 2 full-text summaries and 1 abstract."),
    ([], ""),
])
def test_m4a1_the_basis_note_states_what_was_actually_read(bases, expected):
    """A reader who cannot see that four of six were abstracts will take the
    synthesis as better grounded than it is."""
    included = [{"basis": b} for b in bases]
    assert review.basis_note(included) == expected


def test_m4a2_building_a_prompt_makes_no_network_call(monkeypatch):
    """M4.A.2: a review reads only stored text. That is what makes its cost
    knowable up front — and what keeps this module testable offline (L9)."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the review builder tried to reach the network")

    monkeypatch.setattr(socket, "socket", refuse)
    items = [_item(1, "One", abstract="a"), _item(2, "Two", abstract="b")]
    built = review.build_prompt(review.gather(items, {}))
    assert built["prompt"]
