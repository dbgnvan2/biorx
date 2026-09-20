"""
One PDF of the summaries for the papers in a Saved References list.

Purpose: Render a reference list's papers and their stored summaries as a PDF.
Spec:    docs/implementation_plan_2026-09-18_ui_fixes_summary_pdf.md#SP2-SP5
Tests:   tests/web/test_summaries_pdf.py

Only stored data is printed. A paper with no summary is listed as "Not
summarized" and counted on the first page; nothing is generated here.

Text needs a Unicode font (paper titles carry Greek letters, accents, dashes).
The font is BIORX_PDF_FONT if set, else the first of FONT_CANDIDATES that
exists. With none, the PDF falls back to a built-in Latin-1 font, replaces what
it cannot show, and says how many characters were replaced (P2).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fpdf import FPDF

logger = logging.getLogger(__name__)

FONT_ENV = "BIORX_PDF_FONT"
# Where a Unicode TrueType font usually is: Debian/Ubuntu (the Docker image
# installs fonts-dejavu-core), then macOS.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def find_unicode_font() -> Optional[str]:
    explicit = os.environ.get(FONT_ENV, "").strip()
    if explicit:
        if Path(explicit).is_file():
            return explicit
        logger.warning("%s=%s does not exist — looking elsewhere", FONT_ENV, explicit)
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def register_unicode_font(pdf, font_path: Optional[str] = None) -> tuple:
    """Register the Unicode font on an FPDF and return (family, font_path).

    Shared with src/reference_export.py: both documents need a Unicode font for
    the same reason (paper titles carry Greek, accents and dashes), and one of
    them quietly replacing those characters while the other showed them would
    be worse than either behaviour alone (P5).

    font_path None means "look for one"; a caller that already resolved it, or
    that is deliberately testing the no-font path, passes it in.
    """
    resolved = font_path if font_path is not None else find_unicode_font()
    if resolved:
        pdf.add_font("Body", "", resolved)
        return "Body", resolved
    return "Helvetica", None


def _latin1(value: str) -> tuple:
    """(text a core font can show, number of characters replaced)."""
    out = value.encode("latin-1", errors="replace").decode("latin-1")
    return out, sum(1 for a, b in zip(value, out) if a != b)


def _findings(summary: Dict[str, Any]) -> List[str]:
    raw = summary.get("key_findings")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return [str(raw)] if raw else []
    return [str(x) for x in value if str(x).strip()] if isinstance(value, list) else []


def build_summaries_pdf(list_name: str, items: List[Dict[str, Any]],
                        summaries: Dict[int, Dict[str, Any]],
                        font_path: Optional[str] = None,
                        now: Optional[datetime] = None) -> bytes:
    """items: list_reference_items() rows ({"paper": {...}}); summaries: by paper_id."""
    font_path = font_path if font_path is not None else find_unicode_font()
    papers = [it.get("paper", it) for it in items]
    # Our own punctuation stays within Latin-1 without a Unicode font, so the
    # replaced-character count on the first page is only about paper text.
    bullet, dash = ("•", "—") if font_path else ("-", "-")
    done = sum(1 for p in papers if p.get("paper_id") in summaries)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")

    # Lay the document out as blocks first: the first page reports how many
    # characters could not be shown, which is only known once all text is seen.
    # A block is (text, size, bold, gap_after), or None for a page break.
    def meta_of(p):
        return " · ".join(x for x in (
            (p.get("authors") or "").strip(),
            str(p.get("pub_date") or "").strip(),
            f"DOI {p['doi']}" if p.get("doi") else "",
            (p.get("source") or p.get("server") or "").strip(),
        ) if x)

    # Summarized papers one per page, in list order; the rest together at the
    # end, so a list with few summaries is not mostly empty pages.
    summarized = [p for p in papers if p.get("paper_id") in summaries]
    missing = [p for p in papers if p.get("paper_id") not in summaries]
    body: list = []
    for n, p in enumerate(summarized, 1):
        body.append(None)
        body.append((f"{n}. {p.get('title') or '(untitled)'}", 13, True, 1))
        meta = meta_of(p)
        if meta:
            body.append((meta, 9, False, 3))
        s = summaries[p.get("paper_id")]
        when = str(s.get("created_at") or "")[:10]
        if s.get("source_text") == "abstract":
            # No full text was found, so no model ran: the abstract stands in
            # and must not read as a summary (plan 2026-09-19 C1).
            body.append(("Abstract only — no full text found (not a model summary)",
                         11, True, 0.5))
            body.append(((s.get("summary_text") or "").strip() or "(no abstract)", 10, False, 2))
            continue
        findings = _findings(s)
        if findings:
            body.append(("Key findings", 11, True, 0.5))
            body.extend((f"{bullet}  {f}", 10, False, 0.5) for f in findings)
            body.append(("", 2, False, 1))
        for label, key in (("Methodology", "methodology"), ("Conclusions", "conclusions")):
            if (s.get(key) or "").strip():
                body.append((label, 11, True, 0.5))
                body.append((s[key], 10, False, 2))
        model = s.get("model_version") or "unknown model"
        basis = ""
        if s.get("source_text") == "full_text":
            via = s.get("text_source") or ""
            basis = " from the full text" + (f" (via {via})" if via else "")
        body.append((f"Summary by {model}{basis}" + (f", {when}" if when else "") + ".",
                     8, False, 0))
    if missing:
        body.append(None)
        body.append((f"Not summarized ({len(missing)})", 13, True, 3))
        for p in missing:
            body.append((f"{bullet}  {p.get('title') or '(untitled)'}", 10, False, 0.5))
            meta = meta_of(p)
            if meta:
                body.append((f"    {meta}", 8, False, 2))

    replaced = 0
    if not font_path:
        logger.warning("No Unicode font found (set %s) — non-Latin characters will be replaced",
                       FONT_ENV)
        converted = []
        for block in body:
            if block is None:
                converted.append(None)
                continue
            text, n = _latin1(block[0])
            replaced += n
            converted.append((text,) + block[1:])
        body = converted

    cover = [(list_name or "Saved References", 16, True, 2),
             (f"Summaries {dash} {done} of {len(papers)} papers summarized.", 11, False, 1),
             (f"Exported {stamp}.", 9, False, 1)]
    if len(papers) - done:
        cover.append((f"{len(papers) - done} paper(s) have no summary yet; they are "
                      "listed at the end under \"Not summarized\".", 9, False, 1))
    if replaced:
        cover.append((f"{replaced} character(s) could not be shown in this PDF (no Unicode "
                      f"font on the server; set {FONT_ENV}) and appear as '?'.", 9, False, 1))
    if not font_path:
        cover = [(_latin1(t)[0], sz, b, g) for t, sz, b, g in cover]

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    family, _ = register_unicode_font(pdf, font_path)
    pdf.add_page()
    for block in cover + body:
        if block is None:
            pdf.add_page()
            continue
        text, size, bold, gap = block
        # The Unicode font is registered in one weight; size carries emphasis.
        pdf.set_font(family, "B" if bold and not font_path else "", size)
        if text:
            pdf.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)
    return bytes(pdf.output())
