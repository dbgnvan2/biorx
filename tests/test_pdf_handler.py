"""
PDFHandler download (desktop/CLI path).

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M23
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from src.pdf_handler import PDFHandler


def test_m23_partial_download_not_kept(tmp_path):
    """A transfer that fails part-way leaves no .pdf behind, so the next call
    downloads again instead of treating a truncated file as done."""
    def chunks(chunk_size):
        yield b"%PDF-1.7 first part"
        raise requests.ConnectionError("connection reset")

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.iter_content.side_effect = chunks
    handler = PDFHandler(str(tmp_path))
    with patch("src.pdf_handler.requests.get", return_value=resp):
        assert handler.download_pdf("https://pub.example/x.pdf", "Title", "10.1/x") is None
    assert list(tmp_path.iterdir()) == []


def test_m23_complete_download_is_kept(tmp_path):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.iter_content.return_value = [b"%PDF-1.7 ", b"all of it"]
    handler = PDFHandler(str(tmp_path))
    with patch("src.pdf_handler.requests.get", return_value=resp):
        path = handler.download_pdf("https://pub.example/x.pdf", "Title", "10.1/x")
    assert Path(path).read_bytes() == b"%PDF-1.7 all of it"
    assert not list(tmp_path.glob("*.part"))
