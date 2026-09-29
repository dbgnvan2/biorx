"""
Extract text from an untrusted PDF in a child process, with limits.

Purpose: Keep a hostile PDF (a decompression bomb, thousands of pages, a
         parser hang) from exhausting the web server's memory or CPU.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#B9
Tests:   tests/test_fulltext.py::test_b9_max_pages_enforced,
         tests/test_fulltext.py::test_b9_extraction_timeout,
         tests/test_fulltext.py::test_b9_memory_limit_linux

pdfminer inflates streams without a size limit and holds the GIL while it
parses, and only the first ~20,000 characters are ever used. So: at most
max_pages pages, stop at max_chars, a wall-clock timeout, and on Linux an
address-space limit (RLIMIT_AS; macOS does not enforce it).

Run as a program: python -m src.pdf_extract <path> <max_pages> <max_chars> <mem_bytes>
prints the text to stdout.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# The child's command. Tests replace it to simulate a hanging parser.
WORKER_CMD = [sys.executable, "-m", "src.pdf_extract"]


class ExtractFailed(Exception):
    """The PDF could not be read within the limits. The message says why."""


def _apply_memory_limit(mem_bytes: int) -> None:
    """Called in the child, first thing. (preexec_fn would do it in the parent's
    fork, which the subprocess docs call unsafe in a threaded program — and the
    web server runs job threads.)"""
    if sys.platform.startswith("linux") and mem_bytes > 0:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))


def extract_text_limited(path: str, *, max_pages: int, max_chars: int,
                         timeout: float, mem_bytes: int) -> str:
    """Text of the PDF at `path`, read in a child process under the limits."""
    try:
        done = subprocess.run(
            [*WORKER_CMD, str(path), str(max_pages), str(max_chars), str(mem_bytes)],
            capture_output=True, timeout=timeout, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as e:
        raise ExtractFailed(f"reading the PDF took longer than {timeout:g} s") from e
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        if "MemoryError" in tail[0] or done.returncode in (-9, 137):
            raise ExtractFailed("the PDF needed more memory than allowed")
        raise ExtractFailed(f"the PDF could not be read ({tail[0][:200]})")
    return done.stdout.decode("utf-8", "replace")


def _extract(path: str, max_pages: int, max_chars: int) -> str:
    import pdfplumber
    parts, size = [], 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text() or ""
            parts.append(text)
            size += len(text) + 1
            if size >= max_chars:
                break
    return "\n".join(parts).strip()


def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path, max_pages, max_chars = argv[0], int(argv[1]), int(argv[2])
    _apply_memory_limit(int(argv[3]) if len(argv) > 3 else 0)
    sys.stdout.write(_extract(path, max_pages, max_chars))
    return 0


if __name__ == "__main__":
    sys.exit(main())
