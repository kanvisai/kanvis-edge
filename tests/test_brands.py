"""Tests perfiles de marca y URLs RTSP."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.brands import (
    build_rtsp_template_values,
    list_brand_slugs,
    load_brand_profile,
    render_rtsp_url,
    rtsp_path_from_url,
)
from src.config_loader import AppSettings
from src.discovery.models import (
    CameraBufferSettings,
    CameraOutput,
    CameraRecord,
    CameraSource,
)
from src.discovery.rtsp_urls import build_camera_rtsp_url, default_gateway_path


def test_annke_profile_exists() -> None:
    root = Path(__file__).resolve().parents[1] / "config" / "brands"
    assert "annke" in list_brand_slugs(root)
    profile = load_brand_profile("annke", root)
    assert profile.brand == "Annke"
    assert "{{channel}}" in profile.protocols.rtsp.stream_template


def test_render_annke_stream() -> None:
    root = Path(__file__).resolve().parents[1] / "config" / "brands"
    profile = load_brand_profile("annke", root)
    values = build_rtsp_template_values(
        username="admin",
        password="secret",
        host="10.0.0.5",
        port=554,
        channel="101",
    )
    url = render_rtsp_url(profile, mode="stream", values=values)
    assert url == "rtsp://admin:secret@10.0.0.5:554/Streaming/channels/101"
    assert rtsp_path_from_url(url) == "Streaming/channels/101"


def test_render_annke_playback_utc() -> None:
    root = Path(__file__).resolve().parents[1] / "config" / "brands"
    profile = load_brand_profile("annke", root)
    values = build_rtsp_template_values(
        username="u",
        password="p",
        host="10.0.0.5",
        port=554,
        channel="101",
    )
    start = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    url = render_rtsp_url(
        profile,
        mode="playback",
        values=values,
        starttime=start,
        endtime=end,
    )
    assert "Streaming/tracks/101" in url
    assert "starttime=2024-06-01T10:00:00Z" in url
    assert "endtime=2024-06-01T10:05:00Z" in url


def test_render_tplink_playback_local(monkeypatch) -> None:
    from zoneinfo import ZoneInfo

    from src.brands.time_format import _local_tz

    madrid = ZoneInfo("Europe/Madrid")
    monkeypatch.setattr("src.brands.time_format._local_tz", lambda: madrid)

    root = Path(__file__).resolve().parents[1] / "config" / "brands"
    profile = load_brand_profile("tplink", root)
    values = build_rtsp_template_values(
        username="camera",
        password="camera69",
        host="192.168.1.100",
        port=8554,
        channel="stream1",
    )
    # 20:00 UTC = 22:00 CEST (verano)
    start = datetime(2026, 5, 23, 20, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=36)
    url = render_rtsp_url(
        profile,
        mode="playback",
        values=values,
        starttime=start,
        endtime=end,
    )
    assert "Streaming/tracks/stream1" in url
    assert "starttime=2026-05-23T22:00:00" in url
    assert "endtime=2026-05-23T22:00:36" in url
    assert "Z" not in url.split("?", 1)[1]

    from src.brands.time_format import parse_instant

    parsed_start, _ = (
        parse_instant(
            "2026-05-23T22:00:00",
            time_format=profile.protocols.rtsp.time_format,
            requires_utc=profile.protocols.rtsp.requires_utc,
        ),
        None,
    )
    assert parsed_start == start
    _ = _local_tz


def _annke_camera() -> CameraRecord:
    return CameraRecord(
        camera_id="cam-annke",
        source=CameraSource(
            host="192.168.1.50",
            port=554,
            username="admin",
            password="secret",
            brand="annke",
            channel="101",
        ),
        output=CameraOutput(),
        buffer=CameraBufferSettings(),
    )


def test_camera_device_and_edge_urls() -> None:
    cam = _annke_camera()
    settings = AppSettings()
    device = build_camera_rtsp_url(cam, mode="stream", target="device", settings=settings)
    assert "192.168.1.50:554/Streaming/channels/101" in device
    edge = build_camera_rtsp_url(cam, mode="stream", target="edge", settings=settings)
    assert "127.0.0.1:8554/Streaming/channels/101" in edge


def test_default_gateway_path_annke() -> None:
    cam = _annke_camera()
    settings = AppSettings()
    assert default_gateway_path(cam, settings) == "Streaming/channels/101"
