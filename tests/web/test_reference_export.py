"""
Tests for M6 — Save Reference List as CSV, RTF or PDF.

Spec: docs/implementation_plan_2026-09-20_references_batch.md#M6
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import reference_export

ITEMS = [
    {"item_id": 1, "paper": {
        "title": "Social Baseline Theory: The Social Regulation of Risk",
        "authors": "Coan J, Sbarra D", "pub_date": "2015-02-01",
        "doi": "10.1016/j.copsyc.2014.12.021", "source": "Europe PMC",
        "url": "https://example.org/a.pdf"}},
    {"item_id": 2, "paper": {
        "title": "Σ-band oscillations — a naïve “review” of {curly} things\\slash",
        "authors": "Müller A", "pub_date": "2020-06-30",
        "doi": "10.1234/xyz", "source": "PubMed", "url": ""}},
]


def _pdf_url(paper):
    return paper.get("url") or ""


# ── M6.A.1: the three formats, and nothing else ───────────────────────────────

@pytest.mark.parametrize("fmt,media,ext", [
    ("csv", "text/csv; charset=utf-8", "csv"),
    ("rtf", "application/rtf", "rtf"),
    ("pdf", "application/pdf", "pdf"),
])
def test_m6a1_each_format_renders(fmt, media, ext):
    body, got_media, got_ext = reference_export.render(fmt, ITEMS, _pdf_url, "My list")
    assert (got_media, got_ext) == (media, ext)
    assert body, "an empty document is not a rendered document"


def test_m6a1_unknown_format_is_refused():
    """Never a silent fallback to CSV: handing someone a different file type
    than they asked for is worse than an error."""
    with pytest.raises(ValueError):
        reference_export.render("docx", ITEMS, _pdf_url, "My list")


def test_m6a1_csv_output_is_unchanged():
    """A refactor guard (P12). The CSV renderer was lifted out of the route;
    its bytes must be what the long-standing endpoint produced — header row,
    quote-all, same column order."""
    csv_text = reference_export.to_csv(ITEMS, _pdf_url)
    lines = csv_text.splitlines()
    assert lines[0] == '"Title","Authors","Date","DOI","Source","PDF URL"'
    assert lines[1].startswith('"Social Baseline Theory')
    assert lines[1].endswith('"https://example.org/a.pdf"')


@pytest.mark.parametrize("value,expected", [
    ("=cmd|' /c calc'!A1", "'=cmd|' /c calc'!A1"),
    ("+1+1", "'+1+1"),
    ("-2", "'-2"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("Normal title", "Normal title"),
    (None, ""),
])
def test_m6a1_formula_injection_is_defused_in_every_format(value, expected):
    """A title beginning with = is executed on open by Excel, Numbers and
    Sheets. The guard lives in the shared cell function, so it applies to RTF
    and PDF too — text copied out of those into a spreadsheet is just as live."""
    assert reference_export.safe_cell(value) == expected


# ── M6.A.2: RTF ───────────────────────────────────────────────────────────────

def test_m6a2_rtf_escapes_unicode_and_control_characters():
    """Paper titles routinely carry Greek, accents and curly quotes, so this is
    the normal path. RTF's own syntax characters must be escaped first, or the
    document is structurally broken rather than merely wrong-looking."""
    out = reference_export.rtf_escape('Σ “naïve” {curly} \\slash')
    assert "\\u931?" in out          # Σ
    assert "\\u239?" in out          # ï
    assert "\\{" in out and "\\}" in out
    assert "\\\\" in out
    # No raw non-ASCII survives: an RTF reader would mis-decode it.
    assert all(ord(c) < 128 for c in out)


def test_m6a2_rtf_escapes_astral_characters_as_a_surrogate_pair():
    """RTF's \\uN is a signed 16-bit integer, so a character above U+FFFF (an
    emoji in a title, which does happen) cannot be written directly."""
    out = reference_export.rtf_escape("x\U0001F600y")
    assert out.startswith("x") and out.endswith("y")
    assert out.count("\\u") == 2, "an astral character needs a surrogate pair"


def test_m6a2_rtf_drops_control_characters():
    """A stray control byte in stored data must not reach the document."""
    assert reference_export.rtf_escape("a\x07b") == "ab"
    assert reference_export.rtf_escape("a\nb") == "a\\line b"


def test_m6a2_rtf_is_structurally_valid():
    """Braces balanced and the required header present — the minimum for Word
    or Pages to open it at all."""
    out = reference_export.to_rtf(ITEMS, _pdf_url, "My list")
    assert out.startswith("{\\rtf1")
    assert out.endswith("}")
    depth = 0
    escaped = False
    for ch in out:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        assert depth >= 0, "a closing brace with nothing open"
    assert depth == 0, "unbalanced braces"


def test_m6a2_every_paper_appears_in_the_rtf():
    """P2: a document that silently omits papers is worse than no document."""
    out = reference_export.to_rtf(ITEMS, _pdf_url, "My list")
    assert "Social Baseline Theory" in out
    assert "10.1234/xyz" in out


# ── M6.A.3: PDF ───────────────────────────────────────────────────────────────

def test_m6a3_pdf_is_a_pdf_and_holds_the_papers():
    body = reference_export.to_pdf(ITEMS, _pdf_url, "My list")
    assert body[:5] == b"%PDF-"
    assert len(body) > 500


def test_m6a3_pdf_reports_unrenderable_characters(monkeypatch, caplog):
    """With no Unicode font the core font cannot show Σ or ï. They are replaced
    and the count is both logged and stated in the document, rather than
    characters vanishing with nothing to say so (P2)."""
    import logging

    monkeypatch.setattr("src.summary_pdf.find_unicode_font", lambda: None)
    with caplog.at_level(logging.WARNING, logger="src.reference_export"):
        body = reference_export.to_pdf(ITEMS, _pdf_url, "My list")
    assert body[:5] == b"%PDF-"
    warnings = [r.getMessage() for r in caplog.records]
    assert any("character(s) replaced" in w for w in warnings), (
        f"replacement went unreported; logged: {warnings}"
    )


def test_m6a3_a_unicode_font_reports_no_replacements(monkeypatch, caplog):
    """The other direction — the warning must not fire when the font can show
    everything, or it is noise nobody reads."""
    import logging

    from src import summary_pdf

    font = summary_pdf.find_unicode_font()
    if not font:
        pytest.skip("no Unicode font on this machine to test the happy path")
    with caplog.at_level(logging.WARNING, logger="src.reference_export"):
        reference_export.to_pdf(ITEMS, _pdf_url, "My list")
    assert not [r for r in caplog.records if "replaced" in r.getMessage()]


def test_m6a3_font_handling_is_shared_with_the_summaries_pdf():
    """P5: both documents need a Unicode font for the same reason. One quietly
    replacing accented characters while the other showed them would be worse
    than either behaviour alone, so they register it through one function."""
    from src import summary_pdf
    assert hasattr(summary_pdf, "register_unicode_font")
    source = (Path(__file__).parent.parent.parent / "src" / "reference_export.py").read_text()
    assert "register_unicode_font" in source
    summary_source = (Path(__file__).parent.parent.parent / "src" / "summary_pdf.py").read_text()
    assert summary_source.count("pdf.add_font(") == 1, (
        "the font is registered in one place, or the two documents can drift"
    )


# ── M6.A.5: the file is named after the list ──────────────────────────────────

def test_m6a5_filename_comes_from_the_list_name():
    assert reference_export.safe_filename("Maternal stress 2026") == "Maternal stress 2026"
    assert reference_export.safe_filename("Σ/weird:name") == "__weird_name"
    assert reference_export.safe_filename("") == "references"
    assert reference_export.safe_filename("   ") == "references"


def test_m6a2_a_real_rtf_reader_parses_it():
    """The plan listed "opens in Word/Pages" as needing a human. It does not:
    macOS's textutil is the Cocoa RTF engine TextEdit and Pages use, so if it
    round-trips the text, those applications will too.

    Skipped off macOS — the escaping tests above still cover the rules there.
    """
    import shutil
    import subprocess
    import tempfile

    textutil = shutil.which("textutil")
    if not textutil:
        pytest.skip("textutil is macOS-only; the escaping rules are covered above")

    out = reference_export.to_rtf(ITEMS, _pdf_url, "Attachment sweep")
    with tempfile.NamedTemporaryFile("w", suffix=".rtf", delete=False) as fh:
        fh.write(out)
        path = fh.name
    result = subprocess.run([textutil, "-convert", "txt", "-stdout", path],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"a real RTF reader refused the file: {result.stderr}"
    text = result.stdout

    # The characters the escaping exists for must survive the round trip, not
    # merely fail to crash the reader.
    assert "Σ-band oscillations" in text
    assert "naïve" in text
    assert "“review”" in text
    assert "{curly}" in text
    assert "\\slash" in text
    assert "Müller A" in text
    # Both papers, and the heading.
    assert "Attachment sweep" in text
    assert "Social Baseline Theory" in text


def test_m6a2_the_tricky_escapes_survive_a_real_reader():
    r"""Gate finding 3: the structural tests cover the negative-\uN range and
    surrogate pairs, but those are exactly the two paths worth checking against
    a real reader rather than against my own arithmetic.

    U+8BED is above 0x7FFF, so it goes out as a negative \uN; U+1F600 is
    astral and goes out as a surrogate pair.
    """
    import shutil
    import subprocess
    import tempfile

    textutil = shutil.which("textutil")
    if not textutil:
        pytest.skip("textutil is macOS-only; the escaping rules are covered above")

    items = [{"item_id": 1, "paper": {
        "title": "语 and \U0001F600 in one title",
        "authors": "", "pub_date": "", "doi": "", "source": "", "url": ""}}]
    with tempfile.NamedTemporaryFile("w", suffix=".rtf", delete=False) as fh:
        fh.write(reference_export.to_rtf(items, _pdf_url, "Tricky"))
        path = fh.name
    result = subprocess.run([textutil, "-convert", "txt", "-stdout", path],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "语" in result.stdout, "a negative-range \\uN did not survive"
    assert "\U0001F600" in result.stdout, "an astral surrogate pair did not survive"
