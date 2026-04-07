"""
BioRxiv Research Tool - PyQt6 GUI
Main application with Search & Browse and Filters tabs.
"""

import sys
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem, QCheckBox,
    QSpinBox, QDateEdit, QComboBox, QTextEdit, QSplitter, QMessageBox,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QFormLayout, QGroupBox, QDialog, QInputDialog,
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QThread, QObject
from PyQt6.QtGui import QFont

from agents.search_agent import SearchAgent
from agents.summarization_agent import SummarizationAgent
from src.db import Database
from src.biorxiv_api import BioRxivAPI
from src.pdf_handler import PDFHandler
from src.sources.config import (
    load_sources_config, get_enabled_search_sources,
    get_default_selected_sources, SOURCE_LABELS,
)
from src.sources.orchestrator import SourceOrchestrator

_LOG_FILE = Path("biorx.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

FILTERS_PATH = Path("filters.json")

BIORXIV_CATEGORIES = [
    "(any)",
    "animal behavior and cognition",
    "biochemistry",
    "bioengineering",
    "bioinformatics",
    "biophysics",
    "cancer biology",
    "cell biology",
    "clinical trials",
    "developmental biology",
    "ecology",
    "epidemiology",
    "evolutionary biology",
    "genetics",
    "genomics",
    "immunology",
    "microbiology",
    "molecular biology",
    "neuroscience",
    "paleontology",
    "pathology",
    "pharmacology and toxicology",
    "physiology",
    "plant biology",
    "scientific communication and education",
    "synthetic biology",
    "systems biology",
    "zoology",
]

PAPER_TYPES = ["(any)", "new results", "confirmatory results", "contradictory results", "review article"]
VERSIONS    = ["(any)", "1 (first submission only)", "2+ (revised only)"]
PUBLISHED   = ["(any)", "preprints only (not in journal)", "published in journal only"]
LICENSES    = ["(any)", "cc_by", "cc_by_nc", "cc_by_nd", "cc_no", "pd"]
SPECIES     = [
    "(any)",
    "Human studies only",
    "Exclude animal studies",
    "Animal studies only",
]


def load_filters(path: Path) -> List[Dict[str, Any]]:
    if path.exists():
        with open(path) as f:
            return json.load(f).get("filters", [])
    return []


def save_filters(path: Path, filters: List[Dict[str, Any]]):
    with open(path, "w") as f:
        json.dump({"filters": filters}, f, indent=2)


def _filter_has_text(f: Dict[str, Any]) -> bool:
    """Return True if the filter has at least one non-empty text search term."""
    groups = f.get("text_groups", [])
    for g in groups:
        if any(g.get(k, "").strip() for k in ("title", "abstract", "both")):
            return True
    if any(f.get(k) for k in ("authors", "institution")):
        return True
    return False


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class SearchWorker(QObject):
    batch_ready = pyqtSignal(list)      # matched papers from one API page
    progress    = pyqtSignal(int, int)  # (fetched so far, total)
    status      = pyqtSignal(str)
    finished    = pyqtSignal(list)      # all matched papers
    error       = pyqtSignal(str)

    MAX_PAPERS = 2000

    def __init__(self, orchestrator: "SourceOrchestrator", filter_dict: Dict[str, Any],
                 save_to_db: bool = False, db: Optional[Database] = None):
        super().__init__()
        self.orchestrator = orchestrator
        self.filter_dict  = filter_dict
        self.save_to_db   = save_to_db
        self.db           = db
        self._stop_event  = threading.Event()
        self._all_matched: List[Dict] = []

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            f = self.filter_dict
            self._all_matched = []
            source_selection  = f.get("source_selection", {"all": True, "selected": []})

            def on_batch(records):
                if self._stop_event.is_set():
                    return
                papers  = [r.to_dict() for r in records]
                matched = _filter_papers(papers, f)
                if matched:
                    self._all_matched.extend(matched)
                    self.batch_ready.emit(matched)
                    self.status.emit(
                        f"{len(self._all_matched):,} matched so far…"
                    )

            def on_progress(fetched: int, total: int):
                self.progress.emit(fetched, max(fetched, total))

            self.orchestrator.search(
                filter_dict=f,
                source_selection=source_selection,
                on_batch=on_batch,
                on_progress=on_progress,
                should_stop=self._stop_event.is_set,
                max_results=self.MAX_PAPERS,
            )

            if self._stop_event.is_set():
                self.status.emit(f"Stopped — {len(self._all_matched):,} matched")
            elif self.save_to_db and self.db:
                saved = sum(1 for p in self._all_matched if self.db.insert_paper(p))
                self.status.emit(
                    f"Done — {len(self._all_matched):,} matched, {saved:,} saved"
                )

            self.finished.emit(self._all_matched)
        except Exception as e:
            self.error.emit(str(e))


class SummarizationWorker(QObject):
    finished = pyqtSignal()
    error    = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, agent: SummarizationAgent, paper_id: Optional[int] = None, max_count: int = 10):
        super().__init__()
        self.agent = agent
        self.paper_id = paper_id
        self.max_count = max_count

    def run(self):
        try:
            self.progress.emit("Starting summarization…")
            if self.paper_id:
                ok = self.agent.summarize_paper_by_id(self.paper_id)
                msg = f"Summary generated for paper {self.paper_id}" if ok else f"Failed to summarize paper {self.paper_id}"
                self.progress.emit(msg)
            else:
                result = self.agent.summarize_all_unsummarized(max_count=self.max_count)
                self.progress.emit(f"Complete: {result.get('summarized_count', 0)} papers summarized")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Paper detail dialog + download worker
# ---------------------------------------------------------------------------

def _pdf_url(paper: Dict[str, Any]) -> str:
    """Return best URL for opening a paper's PDF or landing page."""
    # Prefer an explicit PDF/OA URL from enrichment
    if paper.get("pdf_url"):
        return paper["pdf_url"]
    if paper.get("best_oa_url"):
        return paper["best_oa_url"]
    # bioRxiv-style fallback
    doi     = paper.get("doi", "")
    version = paper.get("version", "1")
    source  = paper.get("source", paper.get("server", ""))
    if doi and source in ("biorxiv_medrxiv", "biorxiv", "medrxiv"):
        server = "biorxiv" if "biorxiv" in source else "medrxiv"
        return f"https://www.{server}.org/content/{doi}v{version}.full.pdf"
    if doi:
        return f"https://doi.org/{doi}"
    return paper.get("source_url", paper.get("url", ""))


def _open_in_browser(url: str):
    import webbrowser
    webbrowser.open(url)


def _scrape_abstract_from_url(url: str) -> str:
    """
    Attempt to scrape an abstract from a publisher page.

    Tries in order:
      1. JSON-LD structured data (schema.org/ScholarlyArticle description)
      2. <meta name="description"> / og:description
      3. Common HTML patterns: section/div with id or class containing "abstract"
    Returns empty string if nothing useful is found or the page is paywalled.
    """
    import requests, json, re
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "header", "footer"):
                self._skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "header", "footer"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                self.text_parts.append(data)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        if not resp.ok or len(resp.text) < 500:
            return ""
        html = resp.text
    except Exception as e:
        logger.debug("Abstract scrape fetch failed for %s: %s", url, e)
        return ""

    # 1. JSON-LD structured data
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(m.group(1))
            # May be a list or a single object
            items = data if isinstance(data, list) else [data]
            for item in items:
                # Check top-level and mainEntity (Springer/BMC wraps in WebPage > mainEntity)
                candidates = [item, item.get("mainEntity") or {}]
                for candidate in candidates:
                    desc = candidate.get("description") or candidate.get("abstract") or ""
                    if desc and len(desc) > 80:
                        return re.sub(r"<[^>]+>", "", desc).strip()
        except Exception:
            continue

    # 2. Meta tags
    for pattern in [
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+name=["\']citation_abstract["\']\s+content=["\'](.*?)["\']',
    ]:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = m.group(1).strip()
            if len(text) > 80:
                return re.sub(r"<[^>]+>", "", text).strip()

    # 3. HTML section/div with abstract id or class
    for pattern in [
        r'<(?:section|div|p)[^>]+(?:id|class)=["\'][^"\']*\babstract\b[^"\']*["\'][^>]*>(.*?)</(?:section|div)',
        r'id=["\']Abs1[^"\']*["\'][^>]*>(.*?)</section',
        r'id=["\']abstract["\'][^>]*>(.*?)</(?:section|div)',
    ]:
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = re.sub(r"<[^>]+>", " ", m.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 80:
                return text

    return ""


def _fetch_openalex_abstract(doi: str) -> str:
    """
    Fetch abstract from OpenAlex via its inverted-index format.
    OpenAlex stores abstracts as {word: [position, ...]} dicts to work around
    publisher restrictions; we reconstruct the plain text here.
    Returns empty string on any failure.
    """
    import requests, re
    try:
        clean = re.sub(r"^https?://doi\.org/", "", doi.strip())
        resp = requests.get(
            f"https://api.openalex.org/works/doi:{clean}",
            params={"select": "abstract_inverted_index"},
            headers={"User-Agent": "ResearchTool/1.0 (mailto:research@example.com)"},
            timeout=15,
        )
        if not resp.ok:
            return ""
        idx = resp.json().get("abstract_inverted_index") or {}
        if not idx:
            return ""
        # Reconstruct: sort (position, word) pairs and join
        pairs = sorted(
            (pos, word)
            for word, positions in idx.items()
            for pos in positions
        )
        return " ".join(word for _, word in pairs)
    except Exception as e:
        logger.debug("OpenAlex abstract fetch failed for %s: %s", doi, e)
        return ""


class AbstractFetchWorker(QObject):
    """
    Fetch a missing abstract in a background thread.

    Strategy (in order):
      1. Europe PMC search-by-DOI  — covers most journal papers
      2. Europe PMC full-text XML  — covers open-access PMC papers
      3. Crossref                  — sometimes has JATS-wrapped abstracts
      4. OpenAlex                  — broad coverage, reconstructed from inverted index
      5. HTML scrape of source_url — last resort; tries JSON-LD, meta tags, HTML patterns
    """
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, doi: str, pmcid: str = "", source_url: str = ""):
        super().__init__()
        self.doi        = doi
        self.pmcid      = pmcid
        self.source_url = source_url

    def run(self):
        from src.sources.europepmc import EuropePmcAdapter
        from src.sources.crossref import CrossrefAdapter

        try:
            # 1. Europe PMC by DOI
            if self.doi:
                adapter = EuropePmcAdapter()
                raw = adapter.get_by_id(self.doi)
                if raw:
                    abstract = raw.get("abstractText", "") or ""
                    if abstract:
                        self.finished.emit(abstract)
                        return

                # 2. PMC full-text XML (only if we have a PMCID)
                pmcid = self.pmcid or (raw.get("pmcid", "") if raw else "")
                if pmcid:
                    abstract = adapter.fetch_abstract_from_fulltext(pmcid)
                    if abstract:
                        self.finished.emit(abstract)
                        return

            # 3. Crossref
            if self.doi:
                from src.sources.schema import CanonicalRecord, RecordFlags, make_canonical_id
                cid = make_canonical_id(doi=self.doi, title="", first_author="", year=0)
                record = CanonicalRecord(
                    canonical_id=cid, title="", abstract="",
                    authors=[], year=0, published_date="", document_type="article",
                    is_preprint=False, journal_or_server="", doi=self.doi,
                    pmid="", pmcid="", source_url="", best_oa_url="", pdf_url="",
                    license="", oa_status="", subjects=[], keywords=[],
                    source_hits=[], flags=RecordFlags(),
                )
                CrossrefAdapter().enrich(record)
                if record.abstract:
                    self.finished.emit(record.abstract)
                    return

            # 4. OpenAlex (inverted-index reconstruction)
            if self.doi:
                abstract = _fetch_openalex_abstract(self.doi)
                if abstract:
                    self.finished.emit(abstract)
                    return

            # 5. Scrape the publisher page directly
            urls_to_try = []
            if self.source_url:
                urls_to_try.append(self.source_url)
            if self.doi:
                urls_to_try.append(f"https://doi.org/{self.doi}")
            for url in urls_to_try:
                abstract = _scrape_abstract_from_url(url)
                if abstract:
                    self.finished.emit(abstract)
                    return

            self.finished.emit("(Abstract not available from any source)")
        except Exception as e:
            self.error.emit(str(e))


class BatchPdfDownloadWorker(QObject):
    """Download a list of papers' PDFs sequentially in a background thread."""
    progress = pyqtSignal(int, int, str)   # (done, total, current_title)
    finished = pyqtSignal(int, int)        # (succeeded, failed)
    stopped  = pyqtSignal()

    def __init__(self, papers: list):
        super().__init__()
        self.papers   = papers
        self._stop    = False

    def stop(self):
        self._stop = True

    def run(self):
        from src.pdf_handler import PDFHandler
        handler   = PDFHandler()
        total     = len(self.papers)
        succeeded = 0
        failed    = 0
        for i, paper in enumerate(self.papers):
            if self._stop:
                self.stopped.emit()
                return
            title = paper.get("title", "Untitled")[:60]
            self.progress.emit(i, total, title)
            url = _pdf_url(paper)
            doi = paper.get("doi", "")
            if not url:
                failed += 1
                continue
            try:
                path = handler.download_pdf(url, paper.get("title", ""), doi)
                if path:
                    succeeded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning("Batch PDF download failed for %s: %s", doi, e)
                failed += 1
        self.finished.emit(succeeded, failed)


class PdfSectionWorker(QObject):
    """Extract sections from a local PDF in a background thread."""
    finished = pyqtSignal(str)   # discussion text
    error    = pyqtSignal(str)

    def __init__(self, pdf_path: str):
        super().__init__()
        self.pdf_path = pdf_path

    def run(self):
        from src.pdf_handler import PDFHandler
        try:
            handler    = PDFHandler()
            sections   = handler.extract_sections(self.pdf_path)
            discussion = sections.get("discussion", "").strip()
            if not discussion:
                discussion = "(Discussion section not found — the PDF layout may use non-standard headings)"
            self.finished.emit(discussion)
        except Exception as e:
            self.error.emit(str(e))


class PaperDetailDialog(QDialog):
    """Show abstract and discussion for a paper."""

    def __init__(self, paper: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.paper   = paper
        self._thread = None
        self._worker = None
        self.setWindowTitle("Paper Detail")
        self.setMinimumSize(860, 640)
        self.init_ui()

    def init_ui(self):
        from PyQt6.QtWidgets import QTabWidget, QFileDialog
        layout = QVBoxLayout()

        # ── Header ──
        title_label = QLabel(self.paper.get("title", ""))
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title_label)

        authors = self.paper.get("authors", "")
        if isinstance(authors, list):
            authors = "; ".join(authors)
        meta = (
            f"<b>Authors:</b> {authors}<br>"
            f"<b>Date:</b> {self.paper.get('date') or self.paper.get('pub_date', '')}  "
            f"&nbsp;|&nbsp; <b>Category:</b> {self.paper.get('category', '')}  "
            f"&nbsp;|&nbsp; <b>Type:</b> {self.paper.get('type', '')}  "
            f"&nbsp;|&nbsp; <b>DOI:</b> {self.paper.get('doi', '')}"
        )
        meta_label = QLabel(meta)
        meta_label.setWordWrap(True)
        layout.addWidget(meta_label)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        # ── Tabs: Abstract | Discussion ──
        tabs = QTabWidget()

        # Abstract tab
        abstract_widget = QWidget()
        al = QVBoxLayout()
        self.abstract_text = QTextEdit()
        self.abstract_text.setReadOnly(True)
        abstract = self.paper.get("abstract") or ""
        doi      = self.paper.get("doi", "")
        pmcid    = self.paper.get("pmcid", "")
        if abstract:
            self.abstract_text.setPlainText(abstract)
        elif doi or pmcid:
            self.abstract_text.setPlainText("Retrieving abstract…")
            self._fetch_abstract(doi, pmcid)
        else:
            self.abstract_text.setPlainText("(No abstract available)")
        al.addWidget(self.abstract_text)
        abstract_widget.setLayout(al)
        tabs.addTab(abstract_widget, "Abstract")

        # Discussion tab
        discussion_widget = QWidget()
        dl = QVBoxLayout()
        self.discussion_text = QTextEdit()
        self.discussion_text.setReadOnly(True)
        self.discussion_text.setPlainText(
            "To view the discussion section:\n\n"
            "1. Click 'Open PDF in Browser' — your browser will download the PDF.\n"
            "2. Once downloaded, click 'Load from local PDF…' and select the file.\n\n"
            "The discussion section will be extracted and shown here."
        )
        dl.addWidget(self.discussion_text)

        disc_btn_row = QHBoxLayout()
        open_browser_btn = QPushButton("Open PDF in Browser")
        open_browser_btn.clicked.connect(lambda: _open_in_browser(_pdf_url(self.paper)))
        self.load_local_btn = QPushButton("Load from local PDF…")
        self.load_local_btn.clicked.connect(self._pick_and_extract)
        self.disc_status = QLabel("")
        disc_btn_row.addWidget(open_browser_btn)
        disc_btn_row.addWidget(self.load_local_btn)
        disc_btn_row.addStretch()
        dl.addLayout(disc_btn_row)
        dl.addWidget(self.disc_status)
        discussion_widget.setLayout(dl)
        tabs.addTab(discussion_widget, "Discussion")

        layout.addWidget(tabs, 1)

        # ── Footer ──
        btn_row = QHBoxLayout()
        open_page_btn = QPushButton("Open Paper Page in Browser")
        page_url = (
            self.paper.get("source_url")
            or self.paper.get("url")
            or (f"https://doi.org/{self.paper['doi']}" if self.paper.get("doi") else "")
        )
        open_page_btn.clicked.connect(lambda u=page_url: _open_in_browser(u) if u else None)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(open_page_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _fetch_abstract(self, doi: str, pmcid: str = ""):
        """Fetch abstract in background via DOI/PMCID, update text widget when done."""
        source_url = self.paper.get("source_url") or self.paper.get("url") or ""
        self._abstract_thread = QThread()
        self._abstract_worker = AbstractFetchWorker(doi=doi, pmcid=pmcid, source_url=source_url)
        self._abstract_worker.moveToThread(self._abstract_thread)
        self._abstract_thread.started.connect(self._abstract_worker.run)
        self._abstract_worker.finished.connect(self.abstract_text.setPlainText)
        self._abstract_worker.finished.connect(self._abstract_thread.quit)
        self._abstract_worker.error.connect(
            lambda e: self.abstract_text.setPlainText(f"(Could not fetch abstract: {e})")
        )
        self._abstract_worker.error.connect(self._abstract_thread.quit)
        self._abstract_thread.start()

    def _pick_and_extract(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", str(Path.home()), "PDF files (*.pdf)")
        if not path:
            return
        self.load_local_btn.setEnabled(False)
        self.disc_status.setText("Extracting…")
        self.discussion_text.setPlainText("Extracting discussion section…")

        self._worker = PdfSectionWorker(path)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_extracted)
        self._worker.error.connect(self._on_extract_error)
        self._thread.start()

    def _on_extracted(self, discussion: str):
        self.discussion_text.setPlainText(discussion)
        self.disc_status.setText("Done.")
        self.load_local_btn.setEnabled(True)

    def _on_extract_error(self, err: str):
        self.disc_status.setText(f"Error: {err}")
        self.load_local_btn.setEnabled(True)


def _attach_context_menu(table: "QTableWidget"):
    """Add right-click context menu to a results table. Paper dict must be stored in col-0 UserRole."""
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def on_context_menu(pos):
        from PyQt6.QtWidgets import QMenu
        row = table.rowAt(pos.y())
        if row < 0:
            return
        item = table.item(row, 0)
        if not item:
            return
        paper = item.data(Qt.ItemDataRole.UserRole)
        if not paper:
            return

        menu = QMenu(table)
        view_action     = menu.addAction("View Abstract & Discussion")
        browser_action  = menu.addAction("Open PDF in Browser")
        page_action     = menu.addAction("Open Paper Page in Browser")

        action = menu.exec(table.viewport().mapToGlobal(pos))
        if action == view_action:
            dlg = PaperDetailDialog(paper, table)
            dlg.exec()
        elif action == browser_action:
            _open_in_browser(_pdf_url(paper))
        elif action == page_action:
            url = (
                paper.get("source_url")
                or paper.get("url")
                or (f"https://doi.org/{paper['doi']}" if paper.get("doi") else "")
            )
            if url:
                _open_in_browser(url)

    table.customContextMenuRequested.connect(on_context_menu)


# ---------------------------------------------------------------------------
# Shared filtering logic (used by both tabs)
# ---------------------------------------------------------------------------

def _terms(s: str) -> List[str]:
    """Split comma-separated string into non-empty lowercase terms."""
    return [t.strip().lower() for t in s.split(",") if t.strip()]


def _match(term: str, text: str) -> bool:
    """Match a single term against text. term ending in '*' = begins-with / prefix match."""
    if term.endswith("*"):
        return text.startswith(term[:-1])
    return term in text


def _text_group_matches(paper: Dict[str, Any], group: Dict[str, str]) -> bool:
    """
    A group is a set of AND conditions.
    Each field may have comma-separated terms — any term in that field matches (OR within field).
    Terms ending in '*' use prefix (begins-with) matching.
    All non-empty fields must match (AND between fields).
    """
    title    = (paper.get("title")    or "").lower()
    abstract = (paper.get("abstract") or "").lower()

    title_terms    = _terms(group.get("title",    ""))
    abstract_terms = _terms(group.get("abstract", ""))
    both_terms     = _terms(group.get("both",     ""))

    if title_terms    and not any(_match(t, title)              for t in title_terms):
        return False
    if abstract_terms and not any(_match(t, abstract)           for t in abstract_terms):
        return False
    if both_terms     and not any(_match(t, f"{title} {abstract}") for t in both_terms):
        return False
    return True


def _filter_papers(papers: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply filter criteria to a list of papers. No API calls."""
    text_groups = f.get("text_groups", [])
    if not text_groups and f.get("keywords"):
        text_groups = [{"title": "", "abstract": "", "both": ", ".join(f["keywords"])}]

    authors     = [a.strip() for a in f.get("authors", []) if a.strip()]
    institution = f.get("institution", "").strip().lower()
    paper_type  = f.get("paper_type", "(any)")
    version     = f.get("version", "(any)")
    published   = f.get("published", "(any)")
    license_    = f.get("license", "(any)")

    out = []
    for p in papers:
        auth_str = f"{p.get('authors','').lower()} {p.get('author_corresponding','').lower()}"
        inst_str = (p.get("author_corresponding_institution") or "").lower()
        ptype    = (p.get("type") or "").lower()
        ver      = str(p.get("version") or "")
        pub      = (p.get("published") or "NA")
        lic      = (p.get("license") or "").lower()

        if text_groups and not any(_text_group_matches(p, g) for g in text_groups):
            continue
        if authors and not any(_match(a.lower(), auth_str) for a in authors):
            continue
        if institution and not _match(institution, inst_str):
            continue
        if paper_type != "(any)" and paper_type.lower() not in ptype:
            continue
        if version == "1 (first submission only)" and ver != "1":
            continue
        if version == "2+ (revised only)" and (not ver.isdigit() or int(ver) < 2):
            continue
        if published == "preprints only (not in journal)" and pub != "NA":
            continue
        if published == "published in journal only" and pub == "NA":
            continue
        if license_ != "(any)" and license_.lower() not in lic:
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Source picker widget
# ---------------------------------------------------------------------------

class SourcePickerWidget(QWidget):
    """
    Compact source selection control.
    Shows 'All Sources' + one checkbox per enabled search source.
    Crossref / Unpaywall are enrichment-only and never appear here.
    """
    selection_changed = pyqtSignal(dict)  # {"all": bool, "selected": list[str]}

    def __init__(self, available_sources: List[str], default_selected: List[str],
                 parent=None):
        super().__init__(parent)
        self._available  = available_sources
        self._updating   = False

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self._all_cb = QCheckBox("All Sources")
        self._all_cb.setChecked(True)
        self._all_cb.stateChanged.connect(self._on_all_changed)
        layout.addWidget(self._all_cb)

        self._source_cbs: Dict[str, QCheckBox] = {}
        for source in available_sources:
            label = SOURCE_LABELS.get(source, source)
            cb = QCheckBox(label)
            cb.setChecked(source in default_selected)
            cb.setEnabled(not self._all_cb.isChecked())
            cb.stateChanged.connect(lambda state, s=source: self._on_source_changed(s, state))
            self._source_cbs[source] = cb
            layout.addWidget(cb)

        layout.addStretch()
        self.setLayout(layout)

    def _on_all_changed(self, state: int):
        if self._updating:
            return
        is_checked = state == Qt.CheckState.Checked.value
        self._updating = True
        for cb in self._source_cbs.values():
            cb.setEnabled(not is_checked)
            if is_checked:
                cb.setChecked(False)
        self._updating = False
        self.selection_changed.emit(self.get_selection())

    def _on_source_changed(self, source: str, state: int):
        if self._updating:
            return
        self._updating = True
        if state == Qt.CheckState.Checked.value:
            # At least one individual source selected → uncheck All
            self._all_cb.setChecked(False)
        else:
            # If no individual sources remain checked, restore All
            if not any(cb.isChecked() for cb in self._source_cbs.values()):
                self._all_cb.setChecked(True)
        self._updating = False
        self.selection_changed.emit(self.get_selection())

    def get_selection(self) -> Dict[str, Any]:
        if self._all_cb.isChecked():
            return {"all": True, "selected": []}
        selected = [s for s, cb in self._source_cbs.items() if cb.isChecked()]
        return {"all": False, "selected": selected}

    def load_selection(self, sel: Dict[str, Any]):
        self._updating = True
        use_all = sel.get("all", True)
        selected = sel.get("selected", [])
        self._all_cb.setChecked(use_all)
        for source, cb in self._source_cbs.items():
            cb.setChecked(not use_all and source in selected)
            cb.setEnabled(not use_all)
        self._updating = False


# ---------------------------------------------------------------------------
# Search & Browse tab
# ---------------------------------------------------------------------------

class SearchBrowseTab(QWidget):
    reference_list_saved = pyqtSignal()   # emitted after a new reference list is saved

    def __init__(self, db: Database, orchestrator: "SourceOrchestrator"):
        super().__init__()
        self.db           = db
        self.orchestrator = orchestrator
        self.summ_agent   = SummarizationAgent()
        self.current_results: List[Dict[str, Any]] = []
        self.current_page   = 0
        self.results_per_page = 20
        self._threads: list = []
        # Source picker (reuses orchestrator's enabled sources)
        enabled  = orchestrator.get_enabled_sources()
        defaults = [s for s in enabled]  # all enabled = default for quick search
        self._source_picker = SourcePickerWidget(enabled, defaults)
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout()

        # ── Left panel ──────────────────────────────────────────────────────
        left = QWidget()
        ll   = QVBoxLayout()

        ll.addWidget(QLabel("Saved Filters:"))
        self.filters_list = QListWidget()
        self.filters_list.setMinimumWidth(200)
        ll.addWidget(self.filters_list, 3)
        self.load_filters_list()

        run_layout = QHBoxLayout()
        self.run_selected_btn = QPushButton("Run Selected")
        self.run_selected_btn.clicked.connect(self.run_selected)
        self.run_all_btn = QPushButton("Run All Enabled")
        self.run_all_btn.clicked.connect(self.run_all_enabled)
        run_layout.addWidget(self.run_selected_btn)
        run_layout.addWidget(self.run_all_btn)
        ll.addLayout(run_layout)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); ll.addWidget(sep)

        ll.addWidget(QLabel("Quick Search:"))
        ll.addWidget(QLabel("From:"))
        self.from_date = QDateEdit(); self.from_date.setDate(QDate.currentDate().addDays(-7))
        ll.addWidget(self.from_date)
        ll.addWidget(QLabel("To:"))
        self.to_date = QDateEdit(); self.to_date.setDate(QDate.currentDate())
        ll.addWidget(self.to_date)
        ll.addWidget(QLabel("Category:"))
        self.cat_combo = QComboBox(); self.cat_combo.addItems(BIORXIV_CATEGORIES)
        ll.addWidget(self.cat_combo)
        ll.addWidget(QLabel("Keywords (comma-sep):"))
        self.quick_keywords = QLineEdit()
        ll.addWidget(self.quick_keywords)

        ll.addWidget(QLabel("Sources:"))
        ll.addWidget(self._source_picker)

        quick_btn = QPushButton("Search")
        quick_btn.clicked.connect(self.quick_search)
        ll.addWidget(quick_btn)

        ll.addStretch()
        left.setLayout(ll)

        # ── Right panel ─────────────────────────────────────────────────────
        right  = QWidget()
        rl     = QVBoxLayout()

        self.status_label = QLabel("Ready")
        rl.addWidget(self.status_label)

        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Fetched %v / %m papers")
        self.progress_bar.setVisible(False)
        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop_search)
        prog_row.addWidget(self.progress_bar)
        prog_row.addWidget(self.stop_btn)
        rl.addLayout(prog_row)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(6)
        self.results_table.setHorizontalHeaderLabels(["Title", "Authors", "Date", "Category", "Type", "Source"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setWordWrap(True)
        self.results_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.setToolTip("Right-click a row to view abstract/discussion or download PDF")
        self.results_table.itemChanged.connect(self._on_item_changed)
        _attach_context_menu(self.results_table)
        rl.addWidget(self.results_table, 5)

        # ── Save-selected bar ────────────────────────────────────────────────
        save_bar = QHBoxLayout()
        sel_all_btn = QPushButton("☑ Select All"); sel_all_btn.clicked.connect(self._select_all)
        sel_none_btn = QPushButton("☐ Clear");     sel_none_btn.clicked.connect(self._select_none)
        self._checked_label = QLabel("0 selected")
        self._save_refs_btn = QPushButton("📁  Save as Reference List…")
        self._save_refs_btn.clicked.connect(self._save_selected_as_reference)
        save_bar.addWidget(sel_all_btn)
        save_bar.addWidget(sel_none_btn)
        save_bar.addWidget(self._checked_label)
        save_bar.addStretch()
        save_bar.addWidget(self._save_refs_btn)
        rl.addLayout(save_bar)

        page_layout = QHBoxLayout()
        self.prev_btn = QPushButton("← Prev"); self.prev_btn.clicked.connect(self.prev_page)
        self.next_btn = QPushButton("Next →"); self.next_btn.clicked.connect(self.next_page)
        self.page_label = QLabel("Page 1")
        page_layout.addWidget(self.prev_btn)
        page_layout.addWidget(self.page_label)
        page_layout.addWidget(self.next_btn)
        page_layout.addStretch()
        rl.addLayout(page_layout)

        right.setLayout(rl)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
        self.setLayout(layout)

    def load_filters_list(self):
        self.filters_list.clear()
        for f in load_filters(FILTERS_PATH):
            item = QListWidgetItem(f["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if f.get("enabled", True) else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.filters_list.addItem(item)

    def run_selected(self):
        filters = [
            self.filters_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.filters_list.count())
            if self.filters_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        if not filters:
            QMessageBox.warning(self, "Nothing selected", "Check at least one filter")
            return
        self._run_filters(filters, save_to_db=True)

    def run_all_enabled(self):
        filters = [
            self.filters_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.filters_list.count())
            if self.filters_list.item(i).data(Qt.ItemDataRole.UserRole).get("enabled", True)
        ]
        if not filters:
            QMessageBox.information(self, "No enabled filters", "Enable at least one filter in the Filters tab")
            return
        self._run_filters(filters, save_to_db=True)

    def _run_filters(self, filters: List[Dict], save_to_db: bool = False):
        self.run_selected_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.current_results = []
        self.results_table.setRowCount(0)
        self.current_page = 0
        self._filter_queue  = list(filters)
        self._save_to_db    = save_to_db
        self._total_filters = len(filters)
        self._filter_idx    = 0
        self._current_worker: Optional[SearchWorker] = None
        # Indeterminate bar while we don't know total yet
        self.progress_bar.setMaximum(0)
        self.progress_bar.setFormat("Searching…")
        self.progress_bar.setVisible(True)
        self.stop_btn.setVisible(True)
        self.status_label.setStyleSheet("font-weight: bold; color: #0055aa;")
        self.status_label.setText("⏳  Search started…")
        self._run_next_filter()

    def _stop_search(self):
        self._filter_queue.clear()
        if self._current_worker:
            self._current_worker.stop()
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopping…")

    def _run_next_filter(self):
        if not self._filter_queue:
            self._on_all_filters_done()
            return
        f = self._filter_queue.pop(0)
        self._filter_idx += 1
        name = f.get("name", "")
        # Skip filters with no search terms — they'd return an unfiltered date scan
        if not _filter_has_text(f):
            self.status_label.setText(
                f"⚠  Skipped '{name}': no search terms configured. "
                "Add keywords in the Text Search Groups."
            )
            self._run_next_filter()
            return
        self.status_label.setText(
            f"⏳  Filter {self._filter_idx}/{self._total_filters}: {name}"
        )
        # Stay indeterminate until first progress signal
        self.progress_bar.setMaximum(0)
        self.progress_bar.setFormat("Connecting…")

        worker = SearchWorker(self.orchestrator, f, save_to_db=self._save_to_db, db=self.db)
        self._current_worker = worker
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.batch_ready.connect(self._append_batch)
        worker.progress.connect(self._update_progress)
        worker.status.connect(self.status_label.setText)
        worker.error.connect(lambda e: self.status_label.setText(f"⚠  {e}"))
        worker.finished.connect(lambda _: self._run_next_filter())
        thread.start()
        self._threads.append((thread, worker))

    def _update_progress(self, fetched: int, total: int):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setFormat("Fetched %v / %m papers")
            self.progress_bar.setValue(fetched)

    def _append_batch(self, papers: list):
        self.current_results.extend(papers)
        for paper in papers:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            title_item = QTableWidgetItem(paper.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, paper)
            title_item.setFlags(title_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            title_item.setCheckState(Qt.CheckState.Unchecked)
            self.results_table.setItem(row, 0, title_item)
            authors = paper.get("authors", "")
            if isinstance(authors, list):
                authors = "; ".join(authors)
            self.results_table.setItem(row, 1, QTableWidgetItem(str(authors)[:50]))
            self.results_table.setItem(row, 2, QTableWidgetItem(paper.get("date") or paper.get("pub_date", "")))
            self.results_table.setItem(row, 3, QTableWidgetItem(paper.get("category", "")))
            self.results_table.setItem(row, 4, QTableWidgetItem(paper.get("type", paper.get("document_type", ""))))
            source_label = SOURCE_LABELS.get(paper.get("source", ""), paper.get("source", ""))
            self.results_table.setItem(row, 5, QTableWidgetItem(source_label))
        self.page_label.setText(f"{len(self.current_results):,} papers")
        self._update_checked_count()

    def _on_all_filters_done(self):
        self.progress_bar.setVisible(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setStyleSheet("")
        self.status_label.setText(f"Done — {len(self.current_results):,} papers found")
        self.run_selected_btn.setEnabled(True)
        self.run_all_btn.setEnabled(True)

    def quick_search(self):
        category = self.cat_combo.currentText()
        keywords = [k.strip() for k in self.quick_keywords.text().split(",") if k.strip()]
        days_back = self.from_date.date().daysTo(self.to_date.date()) or 7
        f = {
            "name": "quick",
            "category": category,
            "days_back": days_back,
            "keywords": keywords,
            "text_groups": [{"title": "", "abstract": "", "both": ", ".join(keywords)}] if keywords else [],
            "authors": [],
            "institution": "",
            "paper_type": "(any)",
            "version": "(any)",
            "published": "(any)",
            "license": "(any)",
            "source_selection": self._source_picker.get_selection(),
        }
        self.status_label.setText("Searching…")
        self._run_filters([f], save_to_db=False)

    def display_page(self):
        self.results_table.setRowCount(0)
        start = self.current_page * self.results_per_page
        page  = self.current_results[start:start + self.results_per_page]

        for row, paper in enumerate(page):
            self.results_table.insertRow(row)
            title_item = QTableWidgetItem(paper.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, paper)   # store full dict for right-click
            self.results_table.setItem(row, 0, title_item)
            authors = paper.get("authors", "")
            if isinstance(authors, list):
                authors = "; ".join(authors)
            self.results_table.setItem(row, 1, QTableWidgetItem(str(authors)[:50]))
            self.results_table.setItem(row, 2, QTableWidgetItem(paper.get("date") or paper.get("pub_date", "")))
            self.results_table.setItem(row, 3, QTableWidgetItem(paper.get("category", "")))
            self.results_table.setItem(row, 4, QTableWidgetItem(paper.get("type", "")))

        total = max(1, (len(self.current_results) + self.results_per_page - 1) // self.results_per_page)
        self.page_label.setText(f"Page {self.current_page + 1} of {total}  ({len(self.current_results)} total)")
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < total - 1)

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page()

    def next_page(self):
        total = (len(self.current_results) + self.results_per_page - 1) // self.results_per_page
        if self.current_page < total - 1:
            self.current_page += 1
            self.display_page()

    # ── Checkbox / selection helpers ──────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_checked_count()

    def _update_checked_count(self):
        count = sum(
            1 for r in range(self.results_table.rowCount())
            if (it := self.results_table.item(r, 0)) and it.checkState() == Qt.CheckState.Checked
        )
        self._checked_label.setText(f"{count} selected")

    def _select_all(self):
        self.results_table.itemChanged.disconnect(self._on_item_changed)
        for r in range(self.results_table.rowCount()):
            it = self.results_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked)
        self.results_table.itemChanged.connect(self._on_item_changed)
        self._update_checked_count()

    def _select_none(self):
        self.results_table.itemChanged.disconnect(self._on_item_changed)
        for r in range(self.results_table.rowCount()):
            it = self.results_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked)
        self.results_table.itemChanged.connect(self._on_item_changed)
        self._update_checked_count()

    def _checked_papers(self) -> List[Dict[str, Any]]:
        papers = []
        for r in range(self.results_table.rowCount()):
            it = self.results_table.item(r, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                papers.append(it.data(Qt.ItemDataRole.UserRole))
        return papers

    def _save_selected_as_reference(self):
        papers = self._checked_papers()
        if not papers:
            QMessageBox.information(self, "Nothing selected", "Check at least one paper first.")
            return
        name, ok = QInputDialog.getText(
            self, "Save Reference List", f"Name for this list ({len(papers)} papers):"
        )
        if not ok or not name.strip():
            return
        list_id = self.db.create_reference_list(name.strip())
        if list_id is None:
            QMessageBox.warning(self, "Error", "Could not create reference list.")
            return
        added = sum(1 for p in papers if self.db.add_to_reference_list(list_id, p))
        self._select_none()
        self.reference_list_saved.emit()
        QMessageBox.information(
            self, "Saved",
            f"Saved {added} paper(s) to '{name.strip()}'.\n"
            "Open the Saved References tab to view and download."
        )

    def refresh_filters(self):
        """Called by FiltersTab after a save."""
        self.load_filters_list()


# ---------------------------------------------------------------------------
# Filters tab
# ---------------------------------------------------------------------------
# Text filter group widgets
# ---------------------------------------------------------------------------

class TextFilterGroupWidget(QWidget):
    """
    One AND-condition group. Within the group:
      - Title terms (comma-sep) — any term must appear in title (OR within field)
      - Abstract terms (comma-sep) — any term must appear in abstract
      - Title+Abstract terms (comma-sep) — any term must appear in either
    All non-empty fields must match (AND between fields).
    """
    remove_requested = pyqtSignal(object)

    def __init__(self, data: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        data = data or {}
        self.setObjectName("group")

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)

        # Header row: label + remove button
        header = QHBoxLayout()
        self.label = QLabel("AND group")
        self.label.setStyleSheet("font-weight: bold; color: #555;")
        remove_btn = QPushButton("✕ Remove")
        remove_btn.setFixedWidth(80)
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.label)
        header.addStretch()
        header.addWidget(remove_btn)
        outer.addLayout(header)

        form = QFormLayout()
        self.title_field    = QLineEdit(data.get("title",    ""))
        self.abstract_field = QLineEdit(data.get("abstract", ""))
        self.both_field     = QLineEdit(data.get("both",     ""))

        self.title_field.setPlaceholderText("e.g. hippocampus, memory  (comma = OR)")
        self.abstract_field.setPlaceholderText("e.g. rodent, mouse  (comma = OR)")
        self.both_field.setPlaceholderText("e.g. humans  (comma = OR)")

        form.addRow("Title contains:",           self.title_field)
        form.addRow("Abstract contains:",        self.abstract_field)
        form.addRow("Title OR Abstract contains:", self.both_field)
        outer.addLayout(form)

        # Visual separator
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #ccc;")
        outer.addWidget(line)

        self.setLayout(outer)

    def get_data(self) -> Dict[str, str]:
        return {
            "title":    self.title_field.text().strip(),
            "abstract": self.abstract_field.text().strip(),
            "both":     self.both_field.text().strip(),
        }

    def set_label(self, text: str):
        self.label.setText(text)


class TextFiltersWidget(QWidget):
    """
    Container for multiple TextFilterGroupWidgets.
    Groups are OR'd together — a paper must match at least one.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: List[TextFilterGroupWidget] = []

        self._outer = QVBoxLayout()
        self._outer.setContentsMargins(0, 0, 0, 0)

        self._groups_layout = QVBoxLayout()
        self._groups_layout.setSpacing(4)
        self._outer.addLayout(self._groups_layout)

        add_btn = QPushButton("+ Add OR Group")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(lambda: self.add_group())
        self._outer.addWidget(add_btn)

        self.setLayout(self._outer)

    def add_group(self, data: Dict[str, str] = None):
        widget = TextFilterGroupWidget(data, self)
        widget.remove_requested.connect(self._remove_group)
        self._groups.append(widget)
        self._groups_layout.addWidget(widget)
        self._relabel()

    def _remove_group(self, widget: TextFilterGroupWidget):
        self._groups.remove(widget)
        self._groups_layout.removeWidget(widget)
        widget.deleteLater()
        self._relabel()

    def _relabel(self):
        for i, g in enumerate(self._groups):
            label = "Match if:" if i == 0 else "— OR —"
            g.set_label(label)

    def get_groups(self) -> List[Dict[str, str]]:
        return [g.get_data() for g in self._groups]

    def load_groups(self, groups: List[Dict[str, str]]):
        # Clear existing
        for g in list(self._groups):
            self._groups_layout.removeWidget(g)
            g.deleteLater()
        self._groups.clear()
        for data in groups:
            self.add_group(data)
        if not self._groups:
            self.add_group()   # always show at least one


# ---------------------------------------------------------------------------

class FiltersTab(QWidget):
    """Build, save, test, and manage search filters."""

    filters_changed = pyqtSignal()   # notify Search tab to reload

    def __init__(self, orchestrator: "SourceOrchestrator"):
        super().__init__()
        self.orchestrator = orchestrator
        self.filters: List[Dict[str, Any]] = load_filters(FILTERS_PATH)
        self._current_index = -1
        self._threads: list = []
        # Source picker for filter forms
        enabled  = orchestrator.get_enabled_sources()
        defaults = enabled  # all enabled by default
        self._source_picker = SourcePickerWidget(enabled, defaults)
        self.init_ui()
        self._populate_list()

    # ── UI build ─────────────────────────────────────────────────────────────

    def init_ui(self):
        outer = QHBoxLayout()

        # ── Left: filter list ────────────────────────────────────────────────
        left = QWidget(); left.setMaximumWidth(220)
        ll   = QVBoxLayout()
        ll.addWidget(QLabel("Saved Filters:"))

        self.filter_list = QListWidget()
        self.filter_list.currentRowChanged.connect(self._on_select)
        ll.addWidget(self.filter_list)

        left.setLayout(ll)

        # ── Right: form + test results ───────────────────────────────────────
        right = QWidget()
        rl    = QVBoxLayout()

        # ─ Identity ─
        id_group = QGroupBox("Filter Identity")
        id_form  = QFormLayout()
        self.name_field    = QLineEdit()
        self.enabled_check = QCheckBox("Enabled (will run with 'Run All Enabled')")
        id_form.addRow("Name:", self.name_field)
        id_form.addRow("",      self.enabled_check)
        id_group.setLayout(id_form)
        rl.addWidget(id_group)

        # ─ Scope ─
        scope_group = QGroupBox("Scope")
        scope_form  = QFormLayout()
        self.category_combo = QComboBox(); self.category_combo.addItems(BIORXIV_CATEGORIES)

        # Days-back spinner (default mode)
        self.days_spin = QSpinBox(); self.days_spin.setRange(1, 3650); self.days_spin.setValue(7)

        # Date-range pickers (hidden until toggled on)
        today = QDate.currentDate()
        self.start_date_edit = QDateEdit(today.addDays(-7)); self.start_date_edit.setCalendarPopup(True)
        self.end_date_edit   = QDateEdit(today);             self.end_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")

        # Toggle checkbox
        self.date_range_check = QCheckBox("Use date range")
        self.date_range_check.toggled.connect(self._on_date_range_toggled)

        # Container so we can swap rows cleanly
        self._days_widget  = self.days_spin
        self._range_widget = QWidget()
        range_row = QHBoxLayout(); range_row.setContentsMargins(0, 0, 0, 0)
        range_row.addWidget(QLabel("From:")); range_row.addWidget(self.start_date_edit)
        range_row.addWidget(QLabel("To:"));   range_row.addWidget(self.end_date_edit)
        self._range_widget.setLayout(range_row)
        self._range_widget.setVisible(False)

        scope_form.addRow("Category:",    self.category_combo)
        scope_form.addRow("",             self.date_range_check)
        scope_form.addRow("Days back:",   self._days_widget)
        scope_form.addRow("Date range:",  self._range_widget)
        scope_group.setLayout(scope_form)
        rl.addWidget(scope_group)

        # ─ Sources ─
        sources_group = QGroupBox("Sources")
        sg_layout = QHBoxLayout()
        sg_layout.addWidget(self._source_picker)
        sources_group.setLayout(sg_layout)
        rl.addWidget(sources_group)

        # ─ Text filters ─
        text_group = QGroupBox(
            "Text Search Groups  —  OR between groups, AND within a group  —  comma = OR within a field"
        )
        tg_layout = QVBoxLayout()
        self.text_filters = TextFiltersWidget()
        tg_layout.addWidget(self.text_filters)
        text_group.setLayout(tg_layout)
        rl.addWidget(text_group)

        # ─ Other text filters ─
        other_group = QGroupBox("Author / Institution  (comma-separated — any match wins)")
        other_form  = QFormLayout()
        self.authors_field     = QLineEdit(); self.authors_field.setPlaceholderText("e.g. Smith, Doudna")
        self.institution_field = QLineEdit(); self.institution_field.setPlaceholderText("e.g. Broad Institute")
        other_form.addRow("Authors:",     self.authors_field)
        other_form.addRow("Institution:", self.institution_field)
        other_group.setLayout(other_form)
        rl.addWidget(other_group)

        # ─ Structured filters ─
        struct_group = QGroupBox("Structured Filters")
        struct_form  = QFormLayout()
        self.type_combo      = QComboBox(); self.type_combo.addItems(PAPER_TYPES)
        self.version_combo   = QComboBox(); self.version_combo.addItems(VERSIONS)
        self.published_combo = QComboBox(); self.published_combo.addItems(PUBLISHED)
        self.license_combo   = QComboBox(); self.license_combo.addItems(LICENSES)
        self.species_combo   = QComboBox(); self.species_combo.addItems(SPECIES)
        struct_form.addRow("Paper type:", self.type_combo)
        struct_form.addRow("Version:",    self.version_combo)
        struct_form.addRow("Published:",  self.published_combo)
        struct_form.addRow("License:",    self.license_combo)
        struct_form.addRow("Study type:", self.species_combo)
        struct_group.setLayout(struct_form)
        rl.addWidget(struct_group)

        # ─ Action buttons ─
        action_row = QHBoxLayout()
        new_btn     = QPushButton("+ New");          new_btn.clicked.connect(self._new_filter)
        save_btn    = QPushButton("💾  Save");       save_btn.clicked.connect(self._save_filter)
        save_as_btn = QPushButton("📋  Save As");    save_as_btn.clicked.connect(self._save_as_filter)
        del_btn     = QPushButton("🗑  Delete");     del_btn.clicked.connect(self._delete_filter)
        self.test_btn = QPushButton("▶  Test Filter"); self.test_btn.clicked.connect(self._test_filter)
        action_row.addWidget(new_btn)
        action_row.addWidget(save_btn)
        action_row.addWidget(save_as_btn)
        action_row.addWidget(del_btn)
        action_row.addStretch()
        action_row.addWidget(self.test_btn)
        rl.addLayout(action_row)

        prog_row = QHBoxLayout()
        self.test_status = QLabel("")
        self.test_progress = QProgressBar()
        self.test_progress.setTextVisible(True)
        self.test_progress.setFormat("Fetched %v / %m papers")
        self.test_progress.setVisible(False)
        prog_row.addWidget(self.test_status)
        prog_row.addWidget(self.test_progress)
        rl.addLayout(prog_row)

        # ─ Test results table ─
        rl.addWidget(QLabel("Test Results:"))
        self.test_table = QTableWidget()
        self.test_table.setColumnCount(6)
        self.test_table.setHorizontalHeaderLabels(["Title", "Authors", "Date", "Category", "Type", "Source"])
        self.test_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.test_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.test_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.test_table.setWordWrap(True)
        self.test_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.test_table.setToolTip("Right-click a row to view abstract/discussion or download PDF")
        _attach_context_menu(self.test_table)
        rl.addWidget(self.test_table)

        right.setLayout(rl)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)
        self.setLayout(outer)

    # ── Date-range toggle ─────────────────────────────────────────────────────

    def _on_date_range_toggled(self, checked: bool):
        self._days_widget.setVisible(not checked)
        self._range_widget.setVisible(checked)

    # ── List management ───────────────────────────────────────────────────────

    def _populate_list(self):
        self.filter_list.clear()
        for f in self.filters:
            self.filter_list.addItem(f.get("name", "Unnamed"))
        if self.filters:
            self.filter_list.setCurrentRow(0)

    def _on_select(self, index: int):
        if index < 0 or index >= len(self.filters):
            return
        self._current_index = index
        self._load_form(self.filters[index])

    def _load_form(self, f: Dict[str, Any]):
        self.name_field.setText(f.get("name", ""))
        self.enabled_check.setChecked(f.get("enabled", True))
        cat = f.get("category", "(any)")
        self.category_combo.setCurrentText(cat if cat in BIORXIV_CATEGORIES else "(any)")
        # Date scope
        start = f.get("start_date", "")
        end   = f.get("end_date", "")
        use_range = bool(start and end)
        self.date_range_check.setChecked(use_range)
        if use_range:
            self.start_date_edit.setDate(QDate.fromString(start, "yyyy-MM-dd"))
            self.end_date_edit.setDate(QDate.fromString(end,   "yyyy-MM-dd"))
        else:
            self.days_spin.setValue(f.get("days_back", 7))
        # Load text groups — back-compat: migrate old flat keywords list
        groups = f.get("text_groups", [])
        if not groups and f.get("keywords"):
            groups = [{"title": "", "abstract": "", "both": ", ".join(f["keywords"])}]
        self.text_filters.load_groups(groups)
        self.authors_field.setText(", ".join(f.get("authors", [])))
        self.institution_field.setText(f.get("institution", ""))
        self.type_combo.setCurrentText(f.get("paper_type", "(any)"))
        self.version_combo.setCurrentText(f.get("version", "(any)"))
        self.published_combo.setCurrentText(f.get("published", "(any)"))
        self.license_combo.setCurrentText(f.get("license", "(any)"))
        self.species_combo.setCurrentText(f.get("species", "(any)"))
        self._source_picker.load_selection(
            f.get("source_selection", {"all": True, "selected": []})
        )
        self.test_table.setRowCount(0)
        self.test_status.setText("")

    def _form_to_dict(self) -> Dict[str, Any]:
        use_range = self.date_range_check.isChecked()
        return {
            "name":             self.name_field.text().strip() or "Unnamed",
            "enabled":          self.enabled_check.isChecked(),
            "category":         self.category_combo.currentText(),
            "days_back":        0 if use_range else self.days_spin.value(),
            "start_date":       self.start_date_edit.date().toString("yyyy-MM-dd") if use_range else "",
            "end_date":         self.end_date_edit.date().toString("yyyy-MM-dd")   if use_range else "",
            "text_groups":      self.text_filters.get_groups(),
            "authors":          [a.strip() for a in self.authors_field.text().split(",") if a.strip()],
            "institution":      self.institution_field.text().strip(),
            "paper_type":       self.type_combo.currentText(),
            "version":          self.version_combo.currentText(),
            "published":        self.published_combo.currentText(),
            "license":          self.license_combo.currentText(),
            "species":          self.species_combo.currentText(),
            "source_selection": self._source_picker.get_selection(),
        }

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def _new_filter(self):
        blank = {
            "name": "New Filter", "enabled": True, "category": "(any)",
            "days_back": 7, "start_date": "", "end_date": "",
            "text_groups": [], "authors": [], "institution": "",
            "paper_type": "(any)", "version": "(any)", "published": "(any)", "license": "(any)",
            "species": "(any)", "source_selection": {"all": True, "selected": []},
        }
        self.filters.append(blank)
        save_filters(FILTERS_PATH, self.filters)
        self._populate_list()
        self.filter_list.setCurrentRow(len(self.filters) - 1)

    def _save_filter(self):
        f = self._form_to_dict()
        if not _filter_has_text(f):
            self.test_status.setStyleSheet("font-weight: bold; color: #cc4400;")
            self.test_status.setText("⚠  Cannot save: add at least one search term first.")
            return
        self.test_status.setStyleSheet("")
        # If the name was changed, treat as a new filter rather than overwriting
        original_name = (
            self.filters[self._current_index].get("name", "")
            if self._current_index >= 0 else ""
        )
        name_changed = self._current_index >= 0 and f["name"] != original_name

        if self._current_index < 0 or name_changed:
            self.filters.append(f)
            self._current_index = len(self.filters) - 1
            save_filters(FILTERS_PATH, self.filters)
            self._populate_list()
            self.filter_list.setCurrentRow(self._current_index)
            self.test_status.setText(f"Saved as new filter '{f['name']}'.")
        else:
            self.filters[self._current_index] = f
            save_filters(FILTERS_PATH, self.filters)
            self.test_status.setText("Filter saved.")
        self.filters_changed.emit()

    def _save_as_filter(self):
        """Duplicate current form as a brand-new filter with a prompted name."""
        f = self._form_to_dict()
        if not _filter_has_text(f):
            self.test_status.setStyleSheet("font-weight: bold; color: #cc4400;")
            self.test_status.setText("⚠  Cannot save: add at least one search term first.")
            return
        self.test_status.setStyleSheet("")
        new_name, ok = QInputDialog.getText(
            self, "Save As", "New filter name:", text=f["name"] + " (copy)"
        )
        if not ok or not new_name.strip():
            return
        f["name"] = new_name.strip()
        self.filters.append(f)
        save_filters(FILTERS_PATH, self.filters)
        self._populate_list()
        self._current_index = len(self.filters) - 1
        self.filter_list.setCurrentRow(self._current_index)
        self.test_status.setText(f"Saved as '{f['name']}'.")
        self.filters_changed.emit()

    def _delete_filter(self):
        if self._current_index < 0 or not self.filters:
            return
        name = self.filters[self._current_index].get("name", "this filter")
        if QMessageBox.question(self, "Delete", f"Delete '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        del self.filters[self._current_index]
        save_filters(FILTERS_PATH, self.filters)
        self._current_index = -1
        self._populate_list()
        self.test_table.setRowCount(0)
        self.test_status.setText("")
        self.filters_changed.emit()

    # ── Test ──────────────────────────────────────────────────────────────────

    def _test_filter(self):
        f = self._form_to_dict()
        self.test_table.setRowCount(0)
        if not _filter_has_text(f):
            self.test_status.setStyleSheet("font-weight: bold; color: #cc4400;")
            self.test_status.setText(
                "⚠  No search terms configured. "
                "Add keywords in the Text Search Groups before testing."
            )
            return
        self.test_progress.setMaximum(0)          # indeterminate / pulsing
        self.test_progress.setFormat("Connecting…")
        self.test_progress.setVisible(True)
        self.test_status.setStyleSheet("font-weight: bold; color: #0055aa;")
        self.test_status.setText("⏳  Searching…")
        self.test_btn.setText("⏹  Stop")
        self.test_btn.clicked.disconnect()
        self.test_btn.clicked.connect(self._stop_test)

        self._test_worker = SearchWorker(self.orchestrator, f, save_to_db=False)
        thread = QThread()
        self._test_worker.moveToThread(thread)
        thread.started.connect(self._test_worker.run)
        self._test_worker.batch_ready.connect(self._append_test_batch)
        self._test_worker.progress.connect(self._update_test_progress)
        self._test_worker.status.connect(self.test_status.setText)
        self._test_worker.finished.connect(lambda papers: self._on_test_done(papers))
        self._test_worker.error.connect(lambda e: self._on_test_done([]))
        thread.start()
        self._threads.append((thread, self._test_worker))

    def _stop_test(self):
        if hasattr(self, "_test_worker") and self._test_worker:
            self._test_worker.stop()
        self.test_btn.setEnabled(False)

    def _update_test_progress(self, fetched: int, total: int):
        if total > 0:
            self.test_progress.setMaximum(total)
            self.test_progress.setFormat("Fetched %v / %m papers")
            self.test_progress.setValue(fetched)

    def _append_test_batch(self, papers: list):
        for p in papers:
            row = self.test_table.rowCount()
            self.test_table.insertRow(row)
            title_item = QTableWidgetItem(p.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, p)
            self.test_table.setItem(row, 0, title_item)
            authors = p.get("authors", "")
            if isinstance(authors, list):
                authors = "; ".join(authors)
            self.test_table.setItem(row, 1, QTableWidgetItem(str(authors)[:50]))
            self.test_table.setItem(row, 2, QTableWidgetItem(p.get("date") or p.get("pub_date", "")))
            self.test_table.setItem(row, 3, QTableWidgetItem(p.get("category", "")))
            self.test_table.setItem(row, 4, QTableWidgetItem(p.get("type", p.get("document_type", ""))))
            source_label = SOURCE_LABELS.get(p.get("source", ""), p.get("source", ""))
            self.test_table.setItem(row, 5, QTableWidgetItem(source_label))

    def _on_test_done(self, papers: list):
        self.test_progress.setVisible(False)
        self.test_status.setStyleSheet("")
        self.test_status.setText(f"{len(papers):,} papers matched")
        self.test_btn.setText("▶  Test Filter")
        self.test_btn.setEnabled(True)
        self.test_btn.clicked.disconnect()
        self.test_btn.clicked.connect(self._test_filter)

    def _show_test_results(self, papers: list):
        # Legacy - no longer called directly but kept for safety
        self.test_table.setRowCount(0)
        for row, p in enumerate(papers[:50]):
            self.test_table.insertRow(row)
            title_item = QTableWidgetItem(p.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, p)   # store full dict for right-click
            self.test_table.setItem(row, 0, title_item)
            authors = p.get("authors", "")
            if isinstance(authors, list):
                authors = "; ".join(authors)
            self.test_table.setItem(row, 1, QTableWidgetItem(str(authors)[:50]))
            self.test_table.setItem(row, 2, QTableWidgetItem(p.get("date") or p.get("pub_date", "")))
            self.test_table.setItem(row, 3, QTableWidgetItem(p.get("category", "")))
            self.test_table.setItem(row, 4, QTableWidgetItem(p.get("type", "")))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
# Saved References tab
# ---------------------------------------------------------------------------

class SavedReferencesTab(QWidget):
    """Browse, manage, and batch-download saved reference lists."""

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self._current_list_id: Optional[int] = None
        self._download_thread: Optional[QThread] = None
        self._download_worker: Optional[BatchPdfDownloadWorker] = None
        self.init_ui()
        self.refresh_lists()

    def init_ui(self):
        outer = QHBoxLayout()

        # ── Left: list of saved collections ─────────────────────────────────
        left = QWidget(); left.setMaximumWidth(240)
        ll   = QVBoxLayout()
        ll.addWidget(QLabel("Reference Lists:"))
        self.lists_widget = QListWidget()
        self.lists_widget.currentRowChanged.connect(self._on_list_selected)
        ll.addWidget(self.lists_widget)
        del_list_btn = QPushButton("🗑  Delete List")
        del_list_btn.clicked.connect(self._delete_list)
        ll.addWidget(del_list_btn)
        left.setLayout(ll)

        # ── Right: papers in selected list ───────────────────────────────────
        right = QWidget()
        rl    = QVBoxLayout()

        self.list_title_label = QLabel("Select a list to view its papers")
        self.list_title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        rl.addWidget(self.list_title_label)

        self.papers_table = QTableWidget()
        self.papers_table.setColumnCount(5)
        self.papers_table.setHorizontalHeaderLabels(["Title", "Authors", "Date", "Type", "Source"])
        self.papers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.papers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.papers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.papers_table.setWordWrap(True)
        self.papers_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.papers_table.itemChanged.connect(self._on_paper_item_changed)
        _attach_context_menu(self.papers_table)
        rl.addWidget(self.papers_table, 5)

        # Selection bar
        sel_bar = QHBoxLayout()
        sel_all_btn  = QPushButton("☑ Select All");  sel_all_btn.clicked.connect(self._select_all)
        sel_none_btn = QPushButton("☐ Clear");        sel_none_btn.clicked.connect(self._select_none)
        self._ref_checked_label = QLabel("0 selected")
        sel_bar.addWidget(sel_all_btn)
        sel_bar.addWidget(sel_none_btn)
        sel_bar.addWidget(self._ref_checked_label)
        sel_bar.addStretch()
        remove_btn = QPushButton("Remove Selected from List")
        remove_btn.clicked.connect(self._remove_selected)
        sel_bar.addWidget(remove_btn)
        rl.addLayout(sel_bar)

        # Download bar
        dl_bar = QHBoxLayout()
        dl_sel_btn = QPushButton("⬇  Download Selected PDFs")
        dl_sel_btn.clicked.connect(lambda: self._start_download(selected_only=True))
        dl_all_btn = QPushButton("⬇  Download All PDFs")
        dl_all_btn.clicked.connect(lambda: self._start_download(selected_only=False))
        self._stop_dl_btn = QPushButton("⏹  Stop")
        self._stop_dl_btn.setVisible(False)
        self._stop_dl_btn.clicked.connect(self._stop_download)
        dl_bar.addWidget(dl_sel_btn)
        dl_bar.addWidget(dl_all_btn)
        dl_bar.addStretch()
        dl_bar.addWidget(self._stop_dl_btn)
        rl.addLayout(dl_bar)

        # Progress
        self._dl_status = QLabel("")
        self._dl_progress = QProgressBar()
        self._dl_progress.setTextVisible(True)
        self._dl_progress.setVisible(False)
        rl.addWidget(self._dl_status)
        rl.addWidget(self._dl_progress)

        right.setLayout(rl)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)
        self.setLayout(outer)

    # ── List management ───────────────────────────────────────────────────────

    def refresh_lists(self):
        self.lists_widget.clear()
        for lst in self.db.get_reference_lists():
            item = QListWidgetItem(f"{lst['name']}  ({lst['item_count']})")
            item.setData(Qt.ItemDataRole.UserRole, lst["id"])
            self.lists_widget.addItem(item)

    def _on_list_selected(self, index: int):
        if index < 0:
            self._current_list_id = None
            self.papers_table.setRowCount(0)
            self.list_title_label.setText("Select a list to view its papers")
            return
        item = self.lists_widget.item(index)
        self._current_list_id = item.data(Qt.ItemDataRole.UserRole)
        name = item.text()
        self.list_title_label.setText(name)
        self._load_papers(self._current_list_id)

    def _load_papers(self, list_id: int):
        self.papers_table.itemChanged.disconnect(self._on_paper_item_changed)
        self.papers_table.setRowCount(0)
        for entry in self.db.get_reference_list_items(list_id):
            paper = entry["paper"]
            row   = self.papers_table.rowCount()
            self.papers_table.insertRow(row)
            title_item = QTableWidgetItem(paper.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, {"paper": paper, "item_id": entry["id"]})
            title_item.setFlags(title_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            title_item.setCheckState(Qt.CheckState.Unchecked)
            self.papers_table.setItem(row, 0, title_item)
            authors = paper.get("authors", "")
            if isinstance(authors, list):
                authors = "; ".join(authors)
            self.papers_table.setItem(row, 1, QTableWidgetItem(str(authors)[:50]))
            self.papers_table.setItem(row, 2, QTableWidgetItem(paper.get("date") or paper.get("pub_date", "")))
            self.papers_table.setItem(row, 3, QTableWidgetItem(paper.get("type", paper.get("document_type", ""))))
            source_label = SOURCE_LABELS.get(paper.get("source", ""), paper.get("source", ""))
            self.papers_table.setItem(row, 4, QTableWidgetItem(source_label))
        self.papers_table.itemChanged.connect(self._on_paper_item_changed)
        self._update_checked_count()

    def _delete_list(self):
        if self._current_list_id is None:
            return
        row = self.lists_widget.currentRow()
        name = self.lists_widget.item(row).text() if row >= 0 else "this list"
        if QMessageBox.question(self, "Delete", f"Delete '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_reference_list(self._current_list_id)
        self._current_list_id = None
        self.refresh_lists()
        self.papers_table.setRowCount(0)
        self.list_title_label.setText("Select a list to view its papers")

    # ── Checkbox helpers ──────────────────────────────────────────────────────

    def _on_paper_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_checked_count()

    def _update_checked_count(self):
        count = sum(
            1 for r in range(self.papers_table.rowCount())
            if (it := self.papers_table.item(r, 0)) and it.checkState() == Qt.CheckState.Checked
        )
        self._ref_checked_label.setText(f"{count} selected")

    def _select_all(self):
        self.papers_table.itemChanged.disconnect(self._on_paper_item_changed)
        for r in range(self.papers_table.rowCount()):
            it = self.papers_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked)
        self.papers_table.itemChanged.connect(self._on_paper_item_changed)
        self._update_checked_count()

    def _select_none(self):
        self.papers_table.itemChanged.disconnect(self._on_paper_item_changed)
        for r in range(self.papers_table.rowCount()):
            it = self.papers_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked)
        self.papers_table.itemChanged.connect(self._on_paper_item_changed)
        self._update_checked_count()

    def _checked_entries(self):
        entries = []
        for r in range(self.papers_table.rowCount()):
            it = self.papers_table.item(r, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                entries.append(it.data(Qt.ItemDataRole.UserRole))
        return entries

    def _remove_selected(self):
        entries = self._checked_entries()
        if not entries:
            return
        for e in entries:
            self.db.remove_from_reference_list(e["item_id"])
        if self._current_list_id:
            self._load_papers(self._current_list_id)
            self.refresh_lists()

    # ── Batch download ────────────────────────────────────────────────────────

    def _papers_for_download(self, selected_only: bool) -> List[Dict[str, Any]]:
        if selected_only:
            return [e["paper"] for e in self._checked_entries()]
        return [
            self.papers_table.item(r, 0).data(Qt.ItemDataRole.UserRole)["paper"]
            for r in range(self.papers_table.rowCount())
            if self.papers_table.item(r, 0)
        ]

    def _start_download(self, selected_only: bool):
        papers = self._papers_for_download(selected_only)
        if not papers:
            QMessageBox.information(self, "Nothing to download",
                                    "Select papers or load a list first.")
            return
        self._dl_progress.setMaximum(len(papers))
        self._dl_progress.setValue(0)
        self._dl_progress.setVisible(True)
        self._stop_dl_btn.setVisible(True)
        self._dl_status.setText(f"Starting download of {len(papers)} PDF(s)…")

        self._download_worker = BatchPdfDownloadWorker(papers)
        self._download_thread = QThread()
        self._download_worker.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.progress.connect(self._on_dl_progress)
        self._download_worker.finished.connect(self._on_dl_finished)
        self._download_worker.stopped.connect(self._on_dl_stopped)
        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.stopped.connect(self._download_thread.quit)
        self._download_thread.start()

    def _stop_download(self):
        if self._download_worker:
            self._download_worker.stop()
        self._stop_dl_btn.setEnabled(False)

    def _on_dl_progress(self, done: int, total: int, title: str):
        self._dl_progress.setMaximum(total)
        self._dl_progress.setValue(done)
        self._dl_status.setText(f"Downloading {done + 1}/{total}: {title}…")

    def _on_dl_finished(self, succeeded: int, failed: int):
        self._dl_progress.setVisible(False)
        self._stop_dl_btn.setVisible(True)
        self._stop_dl_btn.setEnabled(True)
        self._stop_dl_btn.setVisible(False)
        msg = f"Done — {succeeded} downloaded"
        if failed:
            msg += f", {failed} failed (no direct PDF URL)"
        self._dl_status.setText(msg)

    def _on_dl_stopped(self):
        self._dl_progress.setVisible(False)
        self._stop_dl_btn.setVisible(False)
        self._stop_dl_btn.setEnabled(True)
        self._dl_status.setText("Download stopped.")


# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Research Tool")
        self.setGeometry(100, 100, 1400, 850)

        self.db = Database()

        # Build orchestrator from config
        sources_config = load_sources_config()
        self.orchestrator = SourceOrchestrator(sources_config)

        tabs = QTabWidget()

        self.search_tab  = SearchBrowseTab(self.db, self.orchestrator)
        self.filters_tab = FiltersTab(self.orchestrator)

        self.saved_refs_tab = SavedReferencesTab(self.db)

        # When filters are saved, refresh the search tab's list
        self.filters_tab.filters_changed.connect(self.search_tab.refresh_filters)
        # When a reference list is saved, refresh the Saved References tab
        self.search_tab.reference_list_saved.connect(self.saved_refs_tab.refresh_lists)

        tabs.addTab(self.search_tab,     "Search & Browse")
        tabs.addTab(self.filters_tab,    "Filters")
        tabs.addTab(self.saved_refs_tab, "Saved References")

        self.setCentralWidget(tabs)
        logger.info("Application started with sources: %s",
                    self.orchestrator.get_enabled_sources())

    def closeEvent(self, event):
        self.db.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
