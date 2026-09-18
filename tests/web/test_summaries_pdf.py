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
    from tests.web.conftest import ACCESS_CODE, account_body
    list_id = _list_with_papers(signed_in, ctx)
    other_client.post("/api/session", json=account_body(ACCESS_CODE))
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


# ── S1–S3: summaries for a search's results (2026-09-18 second plan) ─────────

def _finished_search(signed_in, ctx, papers):
    import time
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    orch = MagicMock()

    def search(on_batch, **_):
        on_batch([SimpleNamespace(to_dict=lambda p=p: dict(p)) for p in papers])
    orch.search = search
    with patch.object(ctx, "get_orchestrator", return_value=orch):
        job_id = signed_in.post("/api/searches", json={
            "filter": {"text_groups": []},
            "source_selection": {"all": True, "selected": []}}).json()["job_id"]
    for _ in range(100):
        if signed_in.get(f"/api/searches/{job_id}").json()["status"] == "done":
            return job_id
        time.sleep(0.02)
    raise AssertionError("search never finished")


def _summarize_stored(ctx, paper, finding, when):
    pid = ctx.db.insert_paper(paper) or ctx.db.find_paper(paper)["id"]
    ctx.db.insert_summary(pid, summary_text="", key_findings=[finding],
                          methodology="m", conclusions="c", model_version="claude-sonnet-5")
    ctx.db.conn.execute("UPDATE summaries SET created_at = ? WHERE paper_id = ?", (when, pid))
    ctx.db.conn.commit()


def test_s1_search_summaries_lists_only_stored_ones_newest_first(signed_in, ctx):
    third = {**UNSUMMARIZED, "title": "Third", "doi": "10.1234/sum.3", "canonical_id": "doi:10.1234/sum.3"}
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED, UNSUMMARIZED, third])
    _summarize_stored(ctx, SUMMARIZED, "older finding", "2026-09-17 10:00:00")
    _summarize_stored(ctx, third, "newer finding", "2026-09-18 10:00:00")
    body = signed_in.get(f"/api/searches/{job_id}/summaries").json()
    assert body["total_results"] == 3
    assert [s["title"] for s in body["summaries"]] == ["Third", SUMMARIZED["title"]]
    assert body["summaries"][0]["key_findings"] == ["newer finding"]


def test_s1_is_read_only_for_unstored_results(signed_in, ctx):
    """A result never stored has no row; listing summaries must not insert one."""
    job_id = _finished_search(signed_in, ctx, [UNSUMMARIZED])
    before = ctx.db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert signed_in.get(f"/api/searches/{job_id}/summaries").json()["summaries"] == []
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == before


def test_s1_other_users_search_is_404(signed_in, other_client, ctx):
    from tests.web.conftest import ACCESS_CODE, account_body
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED])
    other_client.post("/api/session", json=account_body(ACCESS_CODE))
    assert other_client.get(f"/api/searches/{job_id}/summaries").status_code == 404
    assert other_client.post(f"/api/searches/{job_id}/summaries.pdf", json={}).status_code == 404


def test_s3_search_pdf_all_results(signed_in, ctx):
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED, UNSUMMARIZED])
    _summarize_stored(ctx, SUMMARIZED, "the finding", "2026-09-18 10:00:00")
    r = signed_in.post(f"/api/searches/{job_id}/summaries.pdf", json={"title": "Inflammation – 2026-09-18"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    text = _text(r.content)
    assert "Inflammation – 2026-09-18" in text
    assert "1 of 2 papers summarized" in text
    assert "the finding" in text
    assert "Not summarized (1)" in text


def test_s3_search_pdf_only_ticked_papers(signed_in, ctx):
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED, UNSUMMARIZED])
    _summarize_stored(ctx, SUMMARIZED, "the finding", "2026-09-18 10:00:00")
    r = signed_in.post(f"/api/searches/{job_id}/summaries.pdf",
                       json={"paper_ids": [UNSUMMARIZED["canonical_id"]]})
    text = _text(r.content)
    assert "0 of 1 papers summarized" in text
    assert "the finding" not in text        # adversarial: unticked paper left out


def test_s3_unfinished_search_is_409(signed_in, ctx):
    import threading
    from unittest.mock import MagicMock, patch
    release = threading.Event()
    orch = MagicMock()
    orch.search = lambda **_: release.wait(5)
    with patch.object(ctx, "get_orchestrator", return_value=orch):
        job_id = signed_in.post("/api/searches", json={"filter": {"text_groups": []}}).json()["job_id"]
    try:
        assert signed_in.get(f"/api/searches/{job_id}/summaries").status_code == 409
        assert signed_in.post(f"/api/searches/{job_id}/summaries.pdf", json={}).status_code == 409
    finally:
        release.set()


# ── csdp review 2026-09-18 ────────────────────────────────────────────────────

def test_s1_two_results_for_one_paper_list_its_summary_once(signed_in, ctx):
    same_paper = {**SUMMARIZED, "canonical_id": "pmid:999", "source": "pubmed"}
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED, same_paper])
    _summarize_stored(ctx, SUMMARIZED, "the finding", "2026-09-18 10:00:00")
    body = signed_in.get(f"/api/searches/{job_id}/summaries").json()
    assert len(body["summaries"]) == 1


def test_s3_non_latin_title_gives_a_safe_filename(signed_in, ctx):
    """str.isalnum() accepts 中; a Latin-1 header cannot carry it."""
    job_id = _finished_search(signed_in, ctx, [SUMMARIZED])
    r = signed_in.post(f"/api/searches/{job_id}/summaries.pdf", json={"title": "中文 review"})
    assert r.status_code == 200
    assert 'filename="__ review - summaries.pdf"' in r.headers["content-disposition"]
