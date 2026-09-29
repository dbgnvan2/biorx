"""
Plain text from the markup sources put in titles and abstracts.

Purpose: One way to turn a source's HTML/JATS fragment into readable text.
Spec:    docs/cycles/2026-09-29_browser-run.md (finding: Europe PMC abstracts
         showed raw "<h4>Objective</h4>" in the page)
Tests:   tests/test_markup.py

Europe PMC sends abstracts with HTML (<h4>Objective</h4>, <p>, <sub>) and
Crossref with JATS (<jats:p>). The page escapes text, so the tags were shown
as tags. Only known markup tags are removed: scientific text contains "<" and
">" of its own ("p < 0.05 … x > 1"), which a blanket <[^>]+> removal ate.
"""

from __future__ import annotations

import html
import re

# Tag names removed; anything else that looks like "<...>" is left as text.
_INLINE = r"b|i|em|strong|sub|sup|sc|u|span|a|italic|bold|jats:italic|jats:bold|jats:sub|jats:sup|jats:sc"
_BLOCK = r"p|br|div|section|jats:p|jats:sec|abstract|jats:abstract"
_HEADING = r"h[1-6]|title|jats:title"

_OPEN_HEADING = re.compile(rf"<(?:{_HEADING})(?:\s[^<>]*)?>", re.IGNORECASE)
_CLOSE_HEADING = re.compile(rf"</(?:{_HEADING})\s*>", re.IGNORECASE)
_BLOCK_TAG = re.compile(rf"</?(?:{_BLOCK})(?:\s[^<>]*)?/?>", re.IGNORECASE)
_INLINE_TAG = re.compile(rf"</?(?:{_INLINE})(?:\s[^<>]*)?/?>", re.IGNORECASE)


def markup_to_text(text: str) -> str:
    """Readable plain text: a heading becomes "Heading: ", a paragraph a line
    break, inline tags go, entities are decoded. Text with no tags is returned
    unchanged apart from decoding entities and trimming."""
    if not text:
        return ""
    if "<" in text:
        text = _OPEN_HEADING.sub("\n", text)
        text = _CLOSE_HEADING.sub(": ", text)
        text = _BLOCK_TAG.sub("\n", text)
        text = _INLINE_TAG.sub("", text)
        text = re.sub(r"([:.?!])\s*:\s", r"\1 ", text)    # "Methods.: " -> "Methods. "
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
