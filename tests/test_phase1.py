"""Tests Fase 1: relay RTSP y métricas."""

from __future__ import annotations

from src.config_loader import AppSettings
from src.discovery.models import (
    CameraBufferSettings,
    CameraOutput,
    CameraRecord,
    CameraRelayOutput,
    CameraSource,
    OutputProtocol,
)
from src.ingestion.metrics import IngestMetrics
from src.relay.worker import RelayMode, build_ffmpeg_command, build_relay_urls, relay_mode


def _camera(**relay_kw) -> CameraRecord:
    relay = CameraRelayOutput(enabled=True, path_suffix="cam-01", **relay_kw)
    return CameraRecord(
        camera_id="t1",
        source=CameraSource(
            host="192.168.1.10",
            port=554,
            username="admin",
            password="secret",
            path="/stream",
            fps=20,
        ),
        output=CameraOutput(protocol=OutputProtocol.RTSP, relay=relay),
        buffer=CameraBufferSettings(),
    )


def test_relay_listen_url() -> None:
    cam = _camera()
    settings = AppSettings()
    src, out = build_relay_urls(cam, settings)
    assert src.startswith("rtsp://admin:")
    assert out == "rtsp://0.0.0.0:8554/cam-01"


def test_relay_push_url() -> None:
    cam = _camera(
        mode="push",
        push_url="rtsp://cloud.example:554/ingest/t1",
    )
    _, out = build_relay_urls(cam, AppSettings())
    assert out == "rtsp://cloud.example:554/ingest/t1"
    assert relay_mode(cam.output.relay) == RelayMode.PUSH


def test_ffmpeg_command_copy_mode() -> None:
    cam = _camera()
    cmd = build_ffmpeg_command(cam, AppSettings())
    assert "-c:v" in cmd
    assert "copy" in cmd
    assert "-rtsp_flags" in cmd
    assert "listen" in cmd


def test_ffmpeg_command_transcode_gop() -> None:
    cam = _camera(force_transcode_gop=True, iframe_interval_sec=3.0)
    cmd = build_ffmpeg_command(cam, AppSettings())
    assert "libx264" in cmd
    assert "-g" in cmd
    idx = cmd.index("-g")
    assert cmd[idx + 1] == "60"


def test_ingest_metrics() -> None:
    m = IngestMetrics()
    m.on_connected()
    m.on_packet(100)
    snap = m.snapshot()
    assert snap["connected"] is True
    assert snap["packets_total"] == 1
    assert snap["bytes_total"] == 100
