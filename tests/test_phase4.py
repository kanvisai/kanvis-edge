"""Tests Fase 4: auth UI web."""

from __future__ import annotations

import jwt

from src.config_loader import AppSettings
from src.services.webui_auth import (
    WEBUI_TOKEN_AUD,
    create_access_token,
    validate_access_token,
    verify_credentials,
)


def test_webui_credentials() -> None:
    settings = AppSettings(
        WEBUI_USERNAME="admin",
        WEBUI_PASSWORD="secret",
        JWT_SECRET="test-secret-key-32chars-minimum!!",
    )
    assert verify_credentials("admin", "secret", settings)
    assert not verify_credentials("admin", "wrong", settings)


def test_webui_token_roundtrip() -> None:
    settings = AppSettings(
        WEBUI_USERNAME="admin",
        WEBUI_PASSWORD="x",
        JWT_SECRET="test-secret-key-32chars-minimum!!",
        WEBUI_TOKEN_EXPIRE_HOURS=1,
    )
    resp = create_access_token(settings)
    assert validate_access_token(resp.access_token, settings)
    decoded = jwt.decode(
        resp.access_token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=WEBUI_TOKEN_AUD,
    )
    assert decoded["sub"] == "admin"
