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


def test_build_playback_run_on_demand_quotes_http_url() -> None:
    from src.gateway.config import build_playback_run_on_demand

    cmd = build_playback_run_on_demand(AppSettings())
    assert '-i "http://127.0.0.1:' in cmd
    assert "mtx_path=$MTX_PATH&$MTX_QUERY\"" in cmd
    assert "mtx_query=" not in cmd


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


def test_render_yaml_run_on_demand_single_line() -> None:
    from pydantic import SecretStr

    from src.discovery.models import CameraBufferSettings, CameraSource

    settings = AppSettings(RTSP_GATEWAY_ENABLED=True)
    cam = CameraRecord(
        camera_id="cam-tp",
        enabled=True,
        source=CameraSource(
            host="192.168.1.68",
            port=554,
            username="c",
            password=SecretStr("p"),
            brand="tplink",
            channel="stream1",
        ),
        output=CameraOutput(
            protocol=OutputProtocol.RTSP,
            gateway=CameraGatewayOutput(enabled=True, access_mode=ExternalAccessMode.GATEWAY),
        ),
        buffer=CameraBufferSettings(),
    )
    yaml_text = render_mediamtx_yaml(generate_mediamtx_config([cam], settings))
    for line in yaml_text.splitlines():
        if line.strip().startswith("runOnDemand:"):
            assert line.count(" -c copy ") == 1
            assert "&$MTX_QUERY" in line
            return
    raise AssertionError("runOnDemand not found in yaml")


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


def test_replace_rtsp_host_port() -> None:
    from src.gateway.config import replace_rtsp_host_port

    url = "rtsp://user:pass@127.0.0.1:8554/Streaming/tracks/101?starttime=2026-01-01T00:00:00Z"
    out = replace_rtsp_host_port(url, "192.168.1.100", 8554)
    assert "192.168.1.100:8554" in out
    assert "user:pass@" in out
    assert "starttime=" in out


def test_build_gateway_access_urls_annke() -> None:
    from src.gateway.config import build_gateway_access_urls

    settings = AppSettings(RTSP_GATEWAY_ENABLED=True, RTSP_GATEWAY_PORT=8554)
    cam = CameraRecord(
        camera_id="cam-annke",
        enabled=True,
        source=CameraSource(
            host="192.168.1.68",
            port=554,
            username="camera",
            password=SecretStr("camera69"),
            brand="annke",
            channel="101",
            playback_channel="101",
        ),
        output=CameraOutput(
            protocol=OutputProtocol.RTSP,
            gateway=CameraGatewayOutput(enabled=True),
        ),
        buffer=CameraBufferSettings(),
    )
    urls = build_gateway_access_urls(
        cam, settings, lan_host="192.168.1.100", public_host="203.0.113.1"
    )
    assert "error" not in urls
    assert "Streaming/channels/101" in urls["stream"]["url_lan"]
    assert "Streaming/tracks/101" in urls["playback"]["url_lan"]
    assert "starttime=" in urls["playback"]["url_lan"]
    assert "203.0.113.1:55422" in urls["stream"]["url_wan"]
