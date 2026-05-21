"""Tests Fase 3: snapshots y URLs locales."""

from __future__ import annotations

from src.testing.snapshot import local_listen_url


def test_local_listen_url() -> None:
    assert local_listen_url("rtsp://0.0.0.0:8554/cam-01") == "rtsp://127.0.0.1:8554/cam-01"
    assert local_listen_url("rtsp://192.168.1.5:8554/live") == "rtsp://192.168.1.5:8554/live"
