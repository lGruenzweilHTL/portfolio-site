"""Admin-only routes for managing secure download links.

Mounted at /admin/secure-links by main.py. The same Cloudflare Access gate
that protects /admin protects these.

Endpoints:
  GET  /admin/secure-links                -> redirect to /admin (rendered in main view)
  POST /admin/secure-links                -> create a new link
                                            body: { filename, expires_in, max_uses? }
                                            returns the full share URL
  POST /admin/secure-links/<id>/revoke    -> revoke a link (idempotent)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .files import create_secure_link

log = logging.getLogger(__name__)

router = APIRouter()


class CreateLinkRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    # Examples: "30m", "2h", "1d", "3w", or a raw integer (seconds)
    expires_in: str = Field(..., min_length=1, max_length=16)
    max_uses: int = Field(default=5, ge=0, le=10000)


@router.post("/admin/secure-links")
def create_link(req: CreateLinkRequest, request: Request):
    # Auth: same gate as /admin. Reuse the helper from admin.py.
    from .admin import _authenticate
    if _authenticate(request) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        link = create_secure_link(
            req.filename,
            expires_in=req.expires_in,
            max_uses=req.max_uses,
            created_by=_authenticate(request),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return link


@router.post("/admin/secure-links/{link_id}/revoke")
def revoke_link(link_id: int, request: Request):
    from .admin import _authenticate
    from ..db.models import get_db
    if _authenticate(request) is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE secure_links SET revoked = 1 WHERE id = ?", (link_id,)
        )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="link not found")
    return {"ok": True, "id": link_id, "revoked": True}
