"""Tests Fase 6: WAN sync y payloads."""

from __future__ import annotations

from src.config_loader import AppSettings, DDNSProvider
from src.services.wan_sync import WanSyncService


def test_wan_sync_enabled_flags() -> None:
    s = AppSettings.model_construct(ddns_enabled=True, cloud_report_enabled=False)
    assert s.wan_sync_enabled is True
    s2 = AppSettings.model_construct(cloud_report_enabled=True, ddns_enabled=False)
    assert s2.wan_sync_enabled is True
    s3 = AppSettings.model_construct(ddns_enabled=False, cloud_report_enabled=False)
    assert s3.wan_sync_enabled is False


def test_cloud_payload_shape() -> None:
    from pydantic import SecretStr

    settings = AppSettings.model_construct(
        device_name="store-01-edge",
        cloud_access_token=SecretStr("secret-token"),
    )
    svc = WanSyncService(settings)
    payload = svc._cloud_payload("203.0.113.1")
    assert payload == {
        "device_name": "store-01-edge",
        "access_token": "secret-token",
        "public_ip": "203.0.113.1",
    }


def test_cloud_payload_requires_device_name() -> None:
    settings = AppSettings.model_construct(device_name="")
    svc = WanSyncService(settings)
    try:
        svc._cloud_payload("1.2.3.4")
    except ValueError as exc:
        assert "DEVICE_NAME" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_effective_interval() -> None:
    s = AppSettings.model_construct(
        ddns_interval_seconds=120, wan_sync_interval_seconds=0
    )
    assert s.effective_wan_sync_interval == 120
    s2 = AppSettings.model_construct(
        ddns_interval_seconds=120, wan_sync_interval_seconds=60
    )
    assert s2.effective_wan_sync_interval == 60
