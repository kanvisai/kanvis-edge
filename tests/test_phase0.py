"""Tests Fase 0: modelo de config, búfer por tiempo y playback."""

from __future__ import annotations

import time

import pytest

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, CameraSource
from src.ingestion.buffer import PacketCircularBuffer, RawPacket


def test_legacy_camera_migration() -> None:
    legacy = {
        "camera_id": "x1",
        "ip_address": "10.0.0.5",
        "rtsp_port": 554,
        "username": "u",
        "password": "p",
        "rtsp_path": "/stream",
        "fps": 15,
        "resolution": "640x480",
        "enabled": True,
    }
    cam = CameraRecord.from_storage(legacy)
    assert cam.source.host == "10.0.0.5"
    assert cam.source.width == 640
    assert cam.buffer.duration_seconds == 60.0


def test_buffer_prunes_by_duration() -> None:
    buf = PacketCircularBuffer(max_duration_seconds=2.0, max_packets_safety=1000)
    now = time.monotonic()
    for i in range(5):
        buf.append(
            RawPacket(
                data=b"x",
                pts=i,
                dts=i,
                is_keyframe=False,
                captured_at=now - 10 + i * 1.0,
            )
        )
    assert buf.size() <= 3
    assert buf.span_seconds() <= 2.5


def test_snapshot_last_seconds() -> None:
    buf = PacketCircularBuffer(max_duration_seconds=60.0)
    now = time.monotonic()
    for i in range(10):
        buf.append(
            RawPacket(
                data=bytes([i]),
                pts=None,
                dts=None,
                is_keyframe=False,
                captured_at=now - 9 + i,
            )
        )
    window = buf.snapshot_last_seconds(3.0)
    assert len(window) >= 3
    assert all(p.captured_at >= now - 3.5 for p in window)


def test_settings_buffer_defaults() -> None:
    s = AppSettings()
    assert s.buffer_duration_seconds == 60.0
    assert s.default_playback_test_offset_sec == 3.0
