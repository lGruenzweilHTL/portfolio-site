"""Admin API for the chatbot's fallback model list (table chat_models).

Same Cloudflare Access gate as /admin. Endpoints:
  GET    /admin/api/models          -> list, in fallback order
  POST   /admin/api/models          -> {model} appended to the end
  PATCH  /admin/api/models/<id>     -> {enabled?, position?}
  DELETE /admin/api/models/<id>

Writes require `Content-Type: application/json` and, when an Origin header
is present, an Origin that matches this site. Cloudflare Access
authenticates with a cookie, so this keeps cross-site form posts out.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import chat_models
from ..chat import clear_cooldown, cooldown_remaining
from ..config import settings
from .admin import _authenticate

log = logging.getLogger(__name__)

router = APIRouter()


class AddModelRequest(BaseModel):
    model: str = Field(..., max_length=400)


class UpdateModelRequest(BaseModel):
    enabled: bool | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)


def _require_user(request: Request) -> str:
    user = _authenticate(request)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    netloc = urlparse(origin).netloc.lower()
    allowed = {request.headers.get("host", "").lower(), urlparse(settings.base_url).netloc.lower()}
    return bool(netloc) and netloc in allowed


def _require_write(request: Request) -> str:
    """Dependency, so it runs before body validation: a form post gets 415,
    not a 422 about the body."""
    user = _require_user(request)
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="origin not allowed")
    return user


def _with_state(row: dict) -> dict:
    return {**row, "cooldown_s": round(cooldown_remaining(row["model"]))}


@router.get("/admin/api/models")
def list_models(request: Request) -> dict:
    _require_user(request)
    return {"models": [_with_state(m) for m in chat_models.list_models()]}


@router.post("/admin/api/models", status_code=201)
def add_model(req: AddModelRequest, user: str = Depends(_require_write)) -> dict:
    try:
        row = chat_models.add_model(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    clear_cooldown(row["model"])
    log.info("chat model added by %s: %s", user, row["model"])
    return _with_state(row)


@router.patch("/admin/api/models/{model_id}")
def update_model(model_id: int, req: UpdateModelRequest, user: str = Depends(_require_write)) -> dict:
    try:
        row = chat_models.update_model(model_id, enabled=req.enabled, position=req.position)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    if req.enabled:
        # Re-enabled on purpose: give it a fresh try.
        clear_cooldown(row["model"])
    log.info("chat model %s updated by %s: enabled=%s position=%s", row["model"], user, row["enabled"], row["position"])
    return _with_state(row)


@router.delete("/admin/api/models/{model_id}")
def delete_model(model_id: int, user: str = Depends(_require_write)) -> dict:
    try:
        chat_models.delete_model(model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    log.info("chat model %d deleted by %s", model_id, user)
    return {"ok": True, "id": model_id}
