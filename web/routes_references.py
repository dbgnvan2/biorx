"""
Purpose: Per-user reference lists — create, manage, export, and proxy PDFs.
Spec:    docs/web_parity_spec_2026-09-17.md#FP2-B
Tests:   tests/web/test_references_routes.py
"""

from __future__ import annotations

import csv
import ipaddress
import io
import logging
import socket
import urllib.parse
from typing import Any, Dict, List, Optional

import requests as _requests
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from src import user_store
from src.paper_meta import pdf_url as _pdf_url_from_paper

from .auth import current_user, get_context
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()

PDF_MAX_BYTES = 100 * 1024 * 1024   # 100 MB hard ceiling

# ── RFC-1918 / loopback blocks the proxy must never fetch ────────────────────
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except (socket.gaierror, ValueError):
        return False  # can't resolve → let requests fail naturally


def _safe_pdf_url(url: str) -> str:
    """Return url if it is safe to proxy, raise 403 otherwise."""
    if not url.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PDF URL must use https.",
        )
    parsed = urllib.parse.urlparse(url)
    if _is_private_ip(parsed.hostname or ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PDF URL resolves to a private address.",
        )
    return url


# ── Pydantic models ──────────────────────────────────────────────────────────

class CreateListBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddItemBody(BaseModel):
    paper: Dict[str, Any] = Field(default_factory=dict)


# ── List helpers ─────────────────────────────────────────────────────────────

def _get_list_or_404(ctx: AppContext, user_id: str, list_id: int) -> Dict[str, Any]:
    row = user_store.get_reference_list(ctx.db, user_id, list_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such reference list.")
    return row


def _get_item_or_404(ctx: AppContext, list_id: int, item_id: int) -> Dict[str, Any]:
    row = user_store.get_reference_list_item(ctx.db, list_id, item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such item.")
    return row


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/references")
def list_references(ctx: AppContext = Depends(get_context),
                    user_id: str = Depends(current_user)):
    return {"lists": user_store.list_reference_lists(ctx.db, user_id)}


@router.post("/api/references", status_code=status.HTTP_201_CREATED)
def create_reference_list(body: CreateListBody,
                          ctx: AppContext = Depends(get_context),
                          user_id: str = Depends(current_user)):
    list_id = user_store.create_reference_list(ctx.db, user_id, body.name)
    return user_store.get_reference_list(ctx.db, user_id, list_id)


@router.delete("/api/references/{list_id}")
def delete_reference_list(list_id: int,
                          ctx: AppContext = Depends(get_context),
                          user_id: str = Depends(current_user)):
    _get_list_or_404(ctx, user_id, list_id)
    user_store.delete_reference_list(ctx.db, user_id, list_id)
    return {"ok": True}


@router.get("/api/references/{list_id}/items")
def list_reference_items(list_id: int,
                         ctx: AppContext = Depends(get_context),
                         user_id: str = Depends(current_user)):
    _get_list_or_404(ctx, user_id, list_id)
    return {"items": user_store.list_reference_items(ctx.db, list_id)}


@router.post("/api/references/{list_id}/items", status_code=status.HTTP_201_CREATED)
def add_reference_item(list_id: int, body: AddItemBody,
                       ctx: AppContext = Depends(get_context),
                       user_id: str = Depends(current_user)):
    _get_list_or_404(ctx, user_id, list_id)
    if not body.paper:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No paper supplied.")
    paper_id = ctx.db.insert_paper(body.paper)
    if not paper_id:
        existing = ctx.db.find_paper(body.paper)
        paper_id = existing["id"] if existing else None
    if not paper_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Could not store paper.")
    item_id = user_store.add_reference_item(ctx.db, list_id, paper_id)
    return user_store.get_reference_list_item(ctx.db, list_id, item_id)


@router.delete("/api/references/{list_id}/items/{item_id}")
def remove_reference_item(list_id: int, item_id: int,
                           ctx: AppContext = Depends(get_context),
                           user_id: str = Depends(current_user)):
    _get_list_or_404(ctx, user_id, list_id)
    _get_item_or_404(ctx, list_id, item_id)
    user_store.remove_reference_item(ctx.db, list_id, item_id)
    return {"ok": True}


@router.get("/api/references/{list_id}/export.csv")
def export_csv(list_id: int,
               ctx: AppContext = Depends(get_context),
               user_id: str = Depends(current_user)):
    """Download this reference list as a CSV file."""
    ref_list = _get_list_or_404(ctx, user_id, list_id)
    items = user_store.list_reference_items(ctx.db, list_id)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(["Title", "Authors", "Date", "DOI", "Source", "PDF URL"])

    for item in items:
        p = item.get("paper", item)
        title = p.get("title", "")
        authors = p.get("authors", "")
        date = p.get("pub_date", "")
        doi = p.get("doi", "")
        source = p.get("source", p.get("server", ""))
        url = _pdf_url_from_paper(p) or ""

        # Prevent formula injection in spreadsheet apps (P-equivalent: CSV safety)
        def _safe_cell(v: str) -> str:
            v = str(v)
            return "'" + v if v and v[0] in ("=", "+", "-", "@", "\t", "\r") else v

        writer.writerow([_safe_cell(title), _safe_cell(authors), _safe_cell(date),
                         _safe_cell(doi), _safe_cell(source), _safe_cell(url)])

    list_name = ref_list.get("name", "references")
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in list_name)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
    )


@router.get("/api/references/{list_id}/pdf/{paper_id}")
def proxy_pdf(list_id: int, paper_id: int,
              ctx: AppContext = Depends(get_context),
              user_id: str = Depends(current_user)):
    """Proxy a PDF from its source URL.

    Validates: user owns the list, paper_id is in the list, URL is https and
    not private, upstream Content-Length is within PDF_MAX_BYTES.
    """
    _get_list_or_404(ctx, user_id, list_id)

    # paper_id must be an item in this list — ownership of the list is not enough
    item = user_store.get_reference_item_by_paper(ctx.db, list_id, paper_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Paper not in this list.")

    # Fetch the URL from the database — never from the client
    paper = ctx.db.get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Paper not found.")
    url = _pdf_url_from_paper(dict(paper))
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No PDF URL for this paper.")

    _safe_pdf_url(url)   # raises 403 if unsafe

    try:
        resp = _requests.get(url, stream=True, timeout=30,
                             headers={"User-Agent": "BioRx/1.0"})
        resp.raise_for_status()
    except _requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not fetch PDF: {exc}") from exc

    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > PDF_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds {PDF_MAX_BYTES // (1024*1024)} MB limit.",
        )

    data = resp.content
    if len(data) > PDF_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds {PDF_MAX_BYTES // (1024*1024)} MB limit.",
        )

    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="paper.pdf"'},
    )
