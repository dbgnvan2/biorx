"""
A saved reference list, rendered for saving: CSV, RTF or PDF.

Purpose: Turn a reference list's papers into a file the user can keep.
Spec:    docs/implementation_plan_2026-09-20_references_batch.md#M6
Tests:   tests/web/test_reference_export.py

Only stored data is printed; nothing is fetched or generated here.

RTF is written by hand rather than through a library. The format's escaping
rules are small and fixed, and a dependency for three escape rules is a
dependency to keep patched forever.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from .summary_pdf import FONT_ENV

logger = logging.getLogger(__name__)

FORMATS = ("csv", "rtf", "pdf")

COLUMNS = ("Title", "Authors", "Date", "DOI", "Source", "PDF URL")

# A cell starting with one of these is read as a formula by Excel, Numbers and
# Sheets. The guard is not specific to CSV — an RTF or PDF is not executable,
# but a cell copied out of one into a spreadsheet is, so every format uses it.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """A stored value as text, with spreadsheet formula injection defused."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in _FORMULA_LEAD else text


def safe_filename(name: str) -> str:
    """A list name reduced to characters safe in a Content-Disposition header.

    ASCII only: headers are Latin-1, and str.isalnum() also accepts letters
    such as "中" that cannot be encoded there.
    """
    cleaned = "".join(c if (c.isascii() and c.isalnum()) or c in "-_ " else "_"
                      for c in (name or "references"))
    return cleaned.strip() or "references"


def _row(item: Dict[str, Any], pdf_url_of) -> List[str]:
    paper = item.get("paper", item)
    return [safe_cell(paper.get("title")),
            safe_cell(paper.get("authors")),
            safe_cell(paper.get("pub_date")),
            safe_cell(paper.get("doi")),
            safe_cell(paper.get("source") or paper.get("server")),
            safe_cell(pdf_url_of(paper) or "")]


# ── CSV ───────────────────────────────────────────────────────────────────────

def to_csv(items: List[Dict[str, Any]], pdf_url_of) -> str:
    """The same bytes the long-standing export.csv endpoint produced."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(list(COLUMNS))
    for item in items:
        writer.writerow(_row(item, pdf_url_of))
    return buf.getvalue()


# ── RTF ───────────────────────────────────────────────────────────────────────

def rtf_escape(text: str) -> str:
    r"""Escape one string for an RTF document body.

    Three rules, in this order: a backslash and the two brace characters are
    RTF's own syntax and must be escaped first; anything outside ASCII becomes
    a \uN? escape with a literal "?" after it as the fallback character for
    readers that cannot show it. Paper titles routinely carry Greek letters,
    accents and en-dashes, so this path is the normal one, not the exception.

    RTF's \uN takes a SIGNED 16-bit integer, so a code point above 0xFFFF is
    written as its surrogate pair and one above 0x7FFF goes out negative.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if ch in ("\\", "{", "}"):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif code < 0x20:                       # other control characters
            continue
        elif code < 0x80:
            out.append(ch)
        elif code <= 0xFFFF:
            out.append(f"\\u{code if code < 0x8000 else code - 0x10000}?")
        else:
            offset = code - 0x10000             # astral plane: surrogate pair
            high = 0xD800 + (offset >> 10)
            low = 0xDC00 + (offset & 0x3FF)
            out.append(f"\\u{high - 0x10000}?\\u{low - 0x10000}?")
    return "".join(out)


def to_rtf(items: List[Dict[str, Any]], pdf_url_of, list_name: str = "") -> str:
    """A minimal RTF document: a heading, then one block per paper."""
    parts = [r"{\rtf1\ansi\deff0{\fonttbl{\f0 Helvetica;}}", r"\fs28\b ",
             rtf_escape(list_name or "References"), r"\b0\fs20\par\par "]
    for item in items:
        title, authors, date, doi, source, url = _row(item, pdf_url_of)
        parts.append(r"\b " + rtf_escape(title) + r"\b0\par ")
        for label, value in (("Authors", authors), ("Date", date), ("DOI", doi),
                             ("Source", source), ("PDF", url)):
            if value:
                parts.append(rtf_escape(f"{label}: {value}") + r"\par ")
        parts.append(r"\par ")
    parts.append("}")
    return "".join(parts)


# ── PDF ───────────────────────────────────────────────────────────────────────

def to_pdf(items: List[Dict[str, Any]], pdf_url_of, list_name: str = "") -> bytes:
    """A PDF of the list.

    Font handling is shared with the summaries PDF rather than reimplemented:
    both need a Unicode font for the same reason, and one of them silently
    dropping accented characters while the other did not would be worse than
    either behaviour on its own (P5).
    """
    from fpdf import FPDF

    from .summary_pdf import _latin1, register_unicode_font

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    family, font_path = register_unicode_font(pdf)
    pdf.add_page()

    replaced = 0

    def write(text: str, size: int = 10, bold: bool = False) -> None:
        nonlocal replaced
        if not font_path:
            # No Unicode font on this server: a core font cannot encode the
            # text, and characters it drops are counted and reported rather
            # than vanishing silently (P2).
            text, n = _latin1(text)
            replaced += n
        # The Unicode font is registered in one weight; size carries emphasis.
        pdf.set_font(family, "B" if bold and not font_path else "", size)
        pdf.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")

    write(list_name or "References", size=16, bold=True)
    write(f"{len(items)} paper(s)", size=9)
    pdf.ln(4)

    for item in items:
        title, authors, date, doi, source, url = _row(item, pdf_url_of)
        write(title, size=11, bold=True)
        for label, value in (("Authors", authors), ("Date", date), ("DOI", doi),
                             ("Source", source), ("PDF", url)):
            if value:
                write(f"{label}: {value}", size=9)
        pdf.ln(3)

    if replaced:
        pdf.ln(4)
        write(f"{replaced} character(s) could not be shown (no Unicode font on "
              f"the server; set {FONT_ENV}) and appear as '?'.", size=8)
        logger.warning("Reference export: %d character(s) replaced for want of "
                       "a Unicode font", replaced)
    return bytes(pdf.output())


def render(fmt: str, items: List[Dict[str, Any]], pdf_url_of,
           list_name: str = "") -> tuple:
    """(body, media type, file extension) for one of FORMATS.

    An unknown format raises rather than falling back to CSV: silently handing
    someone a different file type than they asked for is worse than an error.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")
    if fmt == "csv":
        return to_csv(items, pdf_url_of), "text/csv; charset=utf-8", "csv"
    if fmt == "rtf":
        return to_rtf(items, pdf_url_of, list_name), "application/rtf", "rtf"
    return to_pdf(items, pdf_url_of, list_name), "application/pdf", "pdf"
