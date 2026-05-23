"""Tests Fase 7: RTSP gateway unificado (MediaMTX config)."""

from __future__ import annotations

from src.config_loader import AppSettings
from pydantic import SecretStr

from src.discovery.models import (
    CameraBufferSettings,
    CameraGatewayOutput,
    CameraOutput,
    CameraRecord,
    CameraRelayOutput,
    CameraSource,
    ExternalAccessMode,
    OutputProtocol,
)
from src.gateway.config import (
    build_gateway_client_url,
    cameras_for_gateway,
    gateway_config_signature,
    generate_mediamtx_config,
    render_mediamtx_yaml,
)


def _camera(
    camera_id: str = "cam-01",
    *,
    gateway_enabled: bool = True,
    access_mode: ExternalAccessMode = ExternalAccessMode.GATEWAY,
    relay_enabled: bool = False,
) -> CameraRecord:
    return CameraRecord(
        camera_id=camera_id,
        enabled=True,
        source=CameraSource(
            host="192.168.100.101",
            port=554,
            username="admin",
            password=SecretStr("secret"),
            path="/Streaming/Channels/101",
        ),
        output=CameraOutput(
            protocol=OutputProtocol.RTSP,
            gateway=CameraGatewayOutput(
                enabled=gateway_enabled,
                access_mode=access_mode,
                path="cam-01",
            ),
            relay=CameraRelayOutput(enabled=relay_enabled),
        ),
        buffer=CameraBufferSettings(),
    )


def test_cameras_for_gateway_excludes_direct() -> None:
    direct = _camera("cam-d", gateway_enabled=False, access_mode=ExternalAccessMode.DIRECT)
    gw = _camera("cam-g", gateway_enabled=True)
    assert cameras_for_gateway([direct, gw]) == [gw]


def test_generate_mediamtx_config_paths() -> None:
    settings = AppSettings(
        RTSP_GATEWAY_ENABLED=True,
        RTSP_GATEWAY_PORT=8554,
    )
    cam = _camera()
    cfg = generate_mediamtx_config([cam], settings)
    assert cfg["rtspAddress"] == "0.0.0.0:8554"
    assert "cam-01" in cfg["paths"]
    assert cfg["paths"]["cam-01"]["source"].startswith("rtsp://admin:")
    assert cfg["paths"]["cam-01"]["sourceOnDemand"] is True


def test_generate_mediamtx_config_brand_playback_path() -> None:
    from pydantic import SecretStr

    from src.discovery.models import CameraBufferSettings, CameraSource

    settings = AppSettings(RTSP_GATEWAY_ENABLED=True, RTSP_GATEWAY_PORT=55411)
    cam = CameraRecord(
        camera_id="cam-annke",
        enabled=True,
        source=CameraSource(
            host="192.168.1.50",
            port=554,
            username="kanvis",
            password=SecretStr("secret"),
            brand="annke",
            channel="101",
        ),
        output=CameraOutput(
            protocol=OutputProtocol.RTSP,
            gateway=CameraGatewayOutput(enabled=True, access_mode=ExternalAccessMode.GATEWAY),
        ),
        buffer=CameraBufferSettings(),
    )
    cfg = generate_mediamtx_config([cam], settings)
    assert "Streaming/channels/101" in cfg["paths"]
    assert "Streaming/tracks/101" in cfg["paths"]
    assert "runOnDemand" in cfg["paths"]["Streaming/tracks/101"]
    assert cfg["paths"]["Streaming/tracks/101"]["runOnDemandRestart"] is False


def test_render_yaml_contains_path() -> None:
    yaml_text = render_mediamtx_yaml(generate_mediamtx_config([_camera()], AppSettings()))
    assert "cam-01:" in yaml_text
    assert "sourceOnDemand" in yaml_text


def test_build_gateway_client_url() -> None:
    url = build_gateway_client_url(_camera(), AppSettings(RTSP_GATEWAY_PORT=8554))
    assert url == "rtsp://127.0.0.1:8554/cam-01"


def test_gateway_config_signature_changes() -> None:
    s1 = gateway_config_signature([_camera()], AppSettings())
    s2 = gateway_config_signature(
        [_camera()],
        AppSettings(RTSP_GATEWAY_PORT=8555),
    )
    assert s1 != s2
