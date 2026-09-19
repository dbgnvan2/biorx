"""
Purpose: Per-user reference lists — create, manage, export, and proxy PDFs.
Spec:    docs/web_parity_spec_2026-09-17.md#FP2-B
Tests:   tests/web/test_references_routes.py
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from src import safe_fetch, user_store
from src.paper_meta import pdf_url as _pdf_url_from_paper

from .auth import current_user, get_context
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()

PDF_MAX_BYTES = 100 * 1024 * 1024   # 100 MB hard ceiling
PDF_TIMEOUT_SECONDS = 30


# ── Pydantic models ──────────────────────────────────────────────────────────

class CreateListBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


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


def _safe_filename(name: str) -> str:
    """A list name reduced to characters safe in a Content-Disposition header.
    ASCII only: headers are Latin-1, and str.isalnum() also accepts letters
    such as "中" that cannot be encoded there."""
    return "".join(c if (c.isascii() and c.isalnum()) or c in "-_ " else "_"
                   for c in (name or "references"))


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/references")
def list_references(ctx: AppContext = Depends(get_context),
                    user_id: str = Depends(current_user)):
    return {"lists": user_store.list_reference_lists(ctx.db, user_id)}


@router.post("/api/references", status_code=status.HTTP_201_CREATED)
def create_reference_list(body: CreateListBody,
                          ctx: AppContext = Depends(get_context),
                          user_id: str = Depends(current_user)):
    try:
        list_id = user_store.create_reference_list(ctx.db, user_id, body.name)
    except user_store.DuplicateListName:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"You already have a list called {body.name!r}.")
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


# There is deliberately no route that adds a client-supplied paper to a list.
# Papers reach a list only through save-as-list, from a search the server ran;
# a client-supplied paper dict would let a user put any URL behind the proxy.


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

    safe_name = _safe_filename(ref_list.get("name", "references"))
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
    )


def _parse_item_ids(item_ids: Optional[str]) -> Optional[set]:
    """"3,7,9" -> {3, 7, 9}; None/"" -> None (all items). 400 on garbage."""
    if not item_ids:
        return None
    try:
        return {int(x) for x in item_ids.split(",") if x.strip()}
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="item_ids must be comma-separated numbers.")


@router.get("/api/references/{list_id}/summaries")
def list_reference_summaries(list_id: int,
                             ctx: AppContext = Depends(get_context),
                             user_id: str = Depends(current_user)):
    """Purpose: Stored summaries for the papers in a saved list, newest first.
    Spec:    docs/implementation_plan_2026-09-18_filter_run.md#RL1
    Tests:   tests/web/test_references_routes.py::test_rl1_list_summaries

    Same shape as GET /api/searches/{id}/summaries, so a saved list shows which
    papers are summarized without re-running the search that found them.
    """
    _get_list_or_404(ctx, user_id, list_id)
    out: List[Dict[str, Any]] = []
    for item in user_store.list_reference_items(ctx.db, list_id):
        p = item["paper"]
        s = ctx.db.get_summary(p["paper_id"])
        if not s:
            continue
        out.append({
            "canonical_id": p.get("canonical_id") or "",
            "doi": p.get("doi") or "",
            "title": p.get("title") or "",
            "paper_id": p["paper_id"],
            "item_id": item["item_id"],
            "key_findings": s.get("key_findings") or [],
            "methodology": s.get("methodology") or "",
            "conclusions": s.get("conclusions") or "",
            "model_version": s.get("model_version") or "",
            "source_text": s.get("source_text") or "",
            "text_source": s.get("text_source") or "",
            "abstract_only": s.get("summary_text") if s.get("source_text") == "abstract" else "",
            "created_at": str(s.get("created_at") or ""),
        })
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return {"summaries": out}


@router.get("/api/references/{list_id}/summaries.pdf")
def export_summaries_pdf(list_id: int,
                         item_ids: Optional[str] = Query(default=None),
                         ctx: AppContext = Depends(get_context),
                         user_id: str = Depends(current_user)):
    """One PDF of this list's papers and their stored summaries.

    Spec: docs/implementation_plan_2026-09-18_ui_fixes_summary_pdf.md#SP1,
          docs/implementation_plan_2026-09-18_filter_run.md#RL3 (item_ids)
    Papers without a summary are listed as "Not summarized" (SP3).
    item_ids ("3,7") limits it to the ticked papers; absent means all.
    """
    from src.summary_pdf import build_summaries_pdf

    ref_list = _get_list_or_404(ctx, user_id, list_id)
    items = user_store.list_reference_items(ctx.db, list_id)
    wanted = _parse_item_ids(item_ids)
    if wanted is not None:
        items = [i for i in items if i["item_id"] in wanted]
        if not items:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="None of the ticked papers are in this list.")
    summaries: Dict[int, Dict[str, Any]] = {}
    for item in items:
        pid = item["paper"]["paper_id"]
        row = ctx.db.get_summary(pid)
        if row:
            summaries[pid] = row
    data = build_summaries_pdf(ref_list.get("name", ""), items, summaries)
    safe_name = _safe_filename(ref_list.get("name", "references"))
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name} - summaries.pdf"'},
    )


@router.get("/api/references/{list_id}/pdf/{paper_id}")
def proxy_pdf(list_id: int, paper_id: int,
              ctx: AppContext = Depends(get_context),
              user_id: str = Depends(current_user)):
    """Proxy a PDF from its source URL.

    Validates: user owns the list and paper_id is in the list. The fetch rules
    (https, public addresses on every redirect hop, size cap, PDF magic bytes)
    live in src/safe_fetch.py.
    """
    _get_list_or_404(ctx, user_id, list_id)

    # paper_id must be an item in this list — ownership of the list is not enough
    item = user_store.get_reference_item_by_paper(ctx.db, list_id, paper_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Paper not in this list.")

    # The URL comes from the papers row. That row is not proof the URL is safe:
    # POST /api/summaries also stores the paper dict a client sends. The fetch
    # rules in safe_fetch are what keep this off internal addresses.
    paper = ctx.db.get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Paper not found.")
    url = _pdf_url_from_paper(dict(paper))
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No PDF URL for this paper.")

    try:
        data = safe_fetch.fetch_pdf(safe_fetch.https_candidate(url), PDF_MAX_BYTES,
                                    timeout=PDF_TIMEOUT_SECONDS)
    except safe_fetch.FetchRefused as exc:
        logger.warning("PDF proxy refused %s: %s", url, exc)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except safe_fetch.NotAPdf as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No PDF available: the link leads to a web page, not a PDF.",
        ) from exc
    except safe_fetch.TooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds {PDF_MAX_BYTES // (1024*1024)} MB limit.",
        ) from exc
    except safe_fetch.FetchFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(exc)) from exc

    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="paper.pdf"'},
    )
