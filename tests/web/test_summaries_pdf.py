"""
Summaries PDF export for a Saved References list.

Spec:  docs/implementation_plan_2026-09-18_ui_fixes_summary_pdf.md#SP1-SP6
The PDF's text is read back with pdfplumber, so these check what a reader
sees, not what the code meant to write.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pdfplumber
import pytest

from src import user_store
from src.summary_pdf import FONT_ENV, build_summaries_pdf, find_unicode_font

REPO = Path(__file__).parent.parent.parent

SUMMARIZED = {"title": "β-amyloid and café consumption — a cohort", "authors": "Smith J; Jones A",
              "pub_date": "2026-01-02", "doi": "10.1234/sum.1",
              "canonical_id": "doi:10.1234/sum.1", "source": "europepmc"}
UNSUMMARIZED = {"title": "A paper nobody summarized", "authors": "Doe K",
                "pub_date": "2026-02-03", "doi": "10.1234/sum.2",
                "canonical_id": "doi:10.1234/sum.2", "source": "pubmed"}


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _list_with_papers(signed_in, ctx, name="Cohort papers"):
    list_id = signed_in.post("/api/references", json={"name": name}).json()["id"]
    ids = []
    for paper in (SUMMARIZED, UNSUMMARIZED):
        pid = ctx.db.insert_paper(paper) or ctx.db.find_paper(paper)["id"]
        user_store.add_reference_item(ctx.db, list_id, pid)
        ids.append(pid)
    ctx.db.insert_summary(ids[0], summary_text="",
                          key_findings=["Coffee intake was not associated with amyloid."],
                          methodology="Prospective cohort, n=1,204.",
                          conclusions="No association after adjustment.",
                          model_version="claude-sonnet-5")
    return list_id


# ── SP1 ───────────────────────────────────────────────────────────────────────

def test_sp1_route_returns_a_pdf(signed_in, ctx):
    list_id = _list_with_papers(signed_in, ctx)
    r = signed_in.get(f"/api/references/{list_id}/summaries.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert 'filename="Cohort papers - summaries.pdf"' in r.headers["content-disposition"]


def test_sp1_another_users_list_is_404(signed_in, other_client, ctx):
    from tests.web.conftest import ACCESS_CODE
    list_id = _list_with_papers(signed_in, ctx)
    other_client.post("/api/session", json={"access_code": ACCESS_CODE, "display_name": "B"})
    assert other_client.get(f"/api/references/{list_id}/summaries.pdf").status_code == 404


def test_sp1_requires_auth(client):
    assert client.get("/api/references/1/summaries.pdf").status_code == 401


# ── SP2 / SP3 ─────────────────────────────────────────────────────────────────

def test_sp2_summarized_paper_shows_metadata_and_summary(signed_in, ctx):
    list_id = _list_with_papers(signed_in, ctx)
    text = _text(signed_in.get(f"/api/references/{list_id}/summaries.pdf").content)
    for expected in ("Smith J; Jones A", "2026-01-02", "DOI 10.1234/sum.1",
                     "Key findings", "Coffee intake was not associated with amyloid.",
                     "Methodology", "Prospective cohort, n=1,204.",
                     "Conclusions", "No association after adjustment.",
                     "Summary by claude-sonnet-5"):
        assert expected in text, expected


def test_sp3_unsummarized_paper_is_listed_not_invented(signed_in, ctx):
    list_id = _list_with_papers(signed_in, ctx)
    text = _text(signed_in.get(f"/api/references/{list_id}/summaries.pdf").content)
    assert "1 of 2 papers summarized" in text
    section = text.split("Not summarized (1)", 1)
    assert len(section) == 2, "no 'Not summarized' section"
    assert "A paper nobody summarized" in section[1]
    assert "A paper nobody summarized" not in section[0]
    # Adversarial: nothing from the other paper's summary is attached to it.
    assert "Key findings" not in section[1] and "Methodology" not in section[1]


def test_sp3_empty_list_still_exports():
    data = build_summaries_pdf("Empty", [], {})
    assert "0 of 0 papers summarized" in _text(data)


# ── SP4 ───────────────────────────────────────────────────────────────────────

def test_sp4_unicode_text_renders_with_a_unicode_font():
    font = find_unicode_font()
    if not font:
        pytest.skip("no Unicode font on this machine")
    items = [{"paper": {**SUMMARIZED, "paper_id": 1}}]
    text = _text(build_summaries_pdf("L", items, {}, font_path=font))
    assert "β-amyloid and café consumption — a cohort" in text
    assert "could not be shown" not in text


def test_sp4_without_a_font_replacements_are_counted_and_stated():
    items = [{"paper": {**SUMMARIZED, "paper_id": 1}}]
    text = _text(build_summaries_pdf("L", items, {}, font_path=""))
    # β and — cannot be shown in Latin-1 (é can): exactly 2, and said so.
    assert "2 character(s) could not be shown" in text
    assert "café" in text


# ── SP5 ───────────────────────────────────────────────────────────────────────

def test_sp5_font_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "Some.ttf"
    fake.write_bytes(b"x")
    monkeypatch.setenv(FONT_ENV, str(fake))
    assert find_unicode_font() == str(fake)
    monkeypatch.setenv(FONT_ENV, str(tmp_path / "missing.ttf"))
    assert find_unicode_font() != str(tmp_path / "missing.ttf")


def test_sp5_docker_image_installs_a_unicode_font():
    from src.summary_pdf import FONT_CANDIDATES
    assert "fonts-dejavu-core" in (REPO / "Dockerfile").read_text()
    assert FONT_CANDIDATES[0] == "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
