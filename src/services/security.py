"""SecurityManager: API Key, JWT nube o sesión UI web."""

from __future__ import annotations

import logging
from typing import Callable

import jwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.status import HTTP_401_UNAUTHORIZED

from src.config_loader import AppSettings, AuthMode
from src.services.webui_auth import validate_access_token

logger = logging.getLogger(__name__)

PUBLIC_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
)
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/api/v1/health",
        "/api/v1/webui/login",
        "/favicon.ico",
    }
)


class SecurityManager:
    """Valida peticiones: API Key (nube), JWT web UI o cookie de sesión."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def _is_public(self, path: str) -> bool:
        if path in PUBLIC_PATHS:
            return True
        if path.startswith("/static/"):
            return True
        return any(path.startswith(p) for p in PUBLIC_PREFIXES)

    def _extract_bearer(self, request: Request) -> str:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.cookies.get("kanvis_token", "")

    def _validate_api_key(self, request: Request) -> bool:
        expected = self._settings.api_key
        if expected is None:
            return False
        header_key = request.headers.get("X-API-Key") or self._extract_bearer(request)
        return header_key == expected.get_secret_value()

    def _validate_jwt_cloud(self, request: Request) -> bool:
        secret = self._settings.jwt_secret
        if secret is None:
            return False
        token = self._extract_bearer(request)
        if not token:
            return False
        try:
            jwt.decode(
                token,
                secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )
            return True
        except jwt.PyJWTError:
            return False

    def _validate_webui_session(self, request: Request) -> bool:
        token = self._extract_bearer(request)
        return validate_access_token(token, self._settings)

    def authenticate(self, request: Request) -> bool:
        if self._is_public(request.url.path):
            return True
        if self._validate_webui_session(request):
            return True
        if self._settings.auth_mode == AuthMode.JWT:
            return self._validate_jwt_cloud(request)
        return self._validate_api_key(request)

    async def middleware(self, request: Request, call_next: Callable) -> Response:
        if not self.authenticate(request):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    status_code=HTTP_401_UNAUTHORIZED,
                    content={"detail": "No autorizado"},
                )
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "Inicia sesión en /"},
            )
        return await call_next(request)
