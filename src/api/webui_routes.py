"""Rutas de autenticación y entrada para la UI web."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from src.services.webui_auth import (
    WebUiLoginRequest,
    WebUiTokenResponse,
    create_access_token,
    validate_access_token,
    verify_credentials,
)

router = APIRouter(tags=["webui"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def _token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("kanvis_token", "")


@router.get("/")
async def webui_index() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="UI no instalada")
    return FileResponse(index, media_type="text/html")


@router.post("/api/v1/webui/login", response_model=WebUiTokenResponse)
async def webui_login(body: WebUiLoginRequest, request: Request) -> WebUiTokenResponse:
    settings = request.app.state.settings
    if not verify_credentials(body.username, body.password, settings):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    return create_access_token(settings)


@router.get("/api/v1/webui/session")
async def webui_session(request: Request) -> dict:
    settings = request.app.state.settings
    token = _token_from_request(request)
    if not validate_access_token(token, settings):
        raise HTTPException(status_code=401, detail="Sesión no válida")
    return {
        "username": settings.webui_username,
        "authenticated": True,
        "device_id": settings.device_id or None,
    }
