"""
PDF download and text extraction utilities.
"""

import requests
import pdfplumber
from pathlib import Path
from typing import Dict, Optional, Tuple
import logging
import re

logger = logging.getLogger(__name__)


class PDFHandler:
    """Handle PDF download and text extraction."""

    def __init__(self, output_dir: str = "~/preprints/PDFs"):
        """
        Initialize PDF handler.

        Args:
            output_dir: Directory to store downloaded PDFs
        """
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_safe_filename(self, title: str, doi: str) -> str:
        """
        Create a safe filename from title and DOI.

        Args:
            title: Paper title
            doi: Paper DOI

        Returns:
            Safe filename
        """
        # Use DOI as base (shorter, unique)
        # Replace slashes with underscores
        safe_doi = doi.replace("/", "_")[:50] if doi else "unknown"

        # Clean title for filename
        safe_title = re.sub(r"[^\w\s-]", "", title[:40])
        safe_title = re.sub(r"[-\s]+", "_", safe_title).strip("_")

        return f"{safe_doi}_{safe_title}.pdf"

    def download_pdf(
        self, url: str, title: str, doi: str, timeout: int = 30
    ) -> Optional[str]:
        """
        Download a PDF from URL.

        Args:
            url: URL to PDF
            title: Paper title (for filename)
            doi: Paper DOI (for filename)
            timeout: Request timeout in seconds

        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            filename = self._get_safe_filename(title, doi)
            filepath = self.output_dir / filename

            # Skip if already downloaded
            if filepath.exists():
                logger.debug(f"PDF already exists: {filepath}")
                return str(filepath)

            response = requests.get(url, timeout=timeout, stream=True)
            response.raise_for_status()

            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            logger.info(f"Downloaded PDF: {filepath}")
            return str(filepath)

        except requests.RequestException as e:
            logger.error(f"Failed to download PDF from {url}: {e}")
            return None
        except IOError as e:
            logger.error(f"Failed to save PDF: {e}")
            return None

    def extract_text(self, pdf_path: str, max_pages: Optional[int] = None) -> str:
        """
        Extract text from a PDF file.

        Args:
            pdf_path: Path to PDF file
            max_pages: Maximum pages to extract (None = all)

        Returns:
            Extracted text
        """
        try:
            text = ""
            with pdfplumber.open(pdf_path) as pdf:
                pages_to_read = len(pdf.pages)
                if max_pages:
                    pages_to_read = min(max_pages, pages_to_read)

                for i in range(pages_to_read):
                    page = pdf.pages[i]
                    text += page.extract_text() or ""
                    text += "\n"

            return text.strip()
        except Exception as e:
            logger.error(f"Failed to extract text from {pdf_path}: {e}")
            return ""

    def extract_sections(self, pdf_path: str) -> Dict[str, str]:
        """
        Extract main sections from a PDF (abstract, introduction, methods, results, discussion, conclusion).

        Args:
            pdf_path: Path to PDF file

        Returns:
            Dictionary with extracted sections
        """
        try:
            full_text = self.extract_text(pdf_path, max_pages=None)
            sections = {
                "full_text": full_text,
                "abstract": "",
                "introduction": "",
                "methods": "",
                "results": "",
                "discussion": "",
                "conclusion": "",
            }

            # Simple heuristic: look for section headers (case-insensitive)
            lines = full_text.split("\n")
            current_section = None
            section_content = []

            section_keywords = {
                "abstract": r"^(abstract|summary)",
                "introduction": r"^(introduction|background)",
                "methods": r"^(methods|methodology|materials and methods)",
                "results": r"^(results|findings)",
                "discussion": r"^(discussion)",
                "conclusion": r"^(conclusion|conclusions|summary)",
            }

            for line in lines:
                line_lower = line.strip().lower()

                # Check if this line is a section header
                matched_section = None
                for section_name, pattern in section_keywords.items():
                    if re.match(pattern, line_lower):
                        # Save previous section
                        if current_section and section_content:
                            sections[current_section] = "\n".join(section_content)
                        current_section = section_name
                        section_content = []
                        matched_section = section_name
                        break

                # Add to current section
                if current_section and not matched_section and line.strip():
                    section_content.append(line)

            # Save final section
            if current_section and section_content:
                sections[current_section] = "\n".join(section_content)

            return sections
        except Exception as e:
            logger.error(f"Failed to extract sections from {pdf_path}: {e}")
            return {"full_text": "", "abstract": ""}


from typing import Dict
