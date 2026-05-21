"""Autenticación local para la UI web de instalación."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
from pydantic import BaseModel

from src.config_loader import AppSettings

logger = logging.getLogger(__name__)

WEBUI_TOKEN_AUD = "kanvis-edge-webui"


class WebUiLoginRequest(BaseModel):
    username: str
    password: str


class WebUiTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _jwt_secret(settings: AppSettings) -> str | None:
    if settings.webui_jwt_secret:
        return settings.webui_jwt_secret.get_secret_value()
    if settings.jwt_secret:
        return settings.jwt_secret.get_secret_value()
    return None


def verify_credentials(username: str, password: str, settings: AppSettings) -> bool:
    expected_user = settings.webui_username
    expected_pass = settings.webui_password
    if not expected_pass:
        logger.warning("WEBUI_PASSWORD no configurada")
        return False
    return username == expected_user and password == expected_pass.get_secret_value()


def create_access_token(settings: AppSettings) -> WebUiTokenResponse:
    secret = _jwt_secret(settings)
    if not secret:
        raise RuntimeError("Configura WEBUI_JWT_SECRET o JWT_SECRET")
    expire_hours = settings.webui_token_expire_hours
    exp = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    payload = {
        "sub": settings.webui_username,
        "aud": WEBUI_TOKEN_AUD,
        "exp": exp,
    }
    token = jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
    return WebUiTokenResponse(
        access_token=token,
        expires_in=int(expire_hours * 3600),
    )


def validate_access_token(token: str, settings: AppSettings) -> bool:
    secret = _jwt_secret(settings)
    if not secret or not token:
        return False
    try:
        jwt.decode(
            token,
            secret,
            algorithms=[settings.jwt_algorithm],
            audience=WEBUI_TOKEN_AUD,
        )
        return True
    except jwt.PyJWTError:
        return False
