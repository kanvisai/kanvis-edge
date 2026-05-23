"""Tests ventana playback RTSP (búfer + vivo + cámara)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from src.ingestion.buffer import PacketCircularBuffer, RawPacket
from src.playback.parse import parse_playback_query_string, playback_query_from_request
from src.playback.window import plan_playback_window


def test_plan_recent_buffer_and_live_tail() -> None:
    """Caso usuario: start 6s atrás, end 24s en el futuro, búfer 60s."""
    now = datetime(2026, 5, 23, 21, 5, 17, tzinfo=timezone.utc)
    start = now - timedelta(seconds=6)
    end = now + timedelta(seconds=24)
    plan = plan_playback_window(
        start=start,
        end=end,
        requested_at=now,
        buffer_depth_sec=60.0,
    )
    assert plan.needs_buffer
    assert abs(plan.buffer_start_sec_ago - 6.0) < 0.01
    assert plan.buffer_end_sec_ago < 0.01
    assert abs(plan.live_tail_sec - 24.0) < 0.01
    assert not plan.needs_camera


def test_plan_historical_camera_only() -> None:
    now = datetime(2026, 5, 23, 21, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(seconds=120)
    end = now - timedelta(seconds=90)
    plan = plan_playback_window(
        start=start,
        end=end,
        requested_at=now,
        buffer_depth_sec=60.0,
    )
    assert not plan.needs_buffer
    assert not plan.needs_live_tail
    assert plan.needs_camera


def test_snapshot_between_ages() -> None:
    buf = PacketCircularBuffer(max_duration_seconds=30.0)
    mono = time.monotonic()
    for i in range(10):
        buf.append(
            RawPacket(
                data=bytes([i]),
                pts=i,
                dts=i,
                is_keyframe=i == 0,
                captured_at=mono - 9 + i,
            )
        )
    window = buf.snapshot_between_ages(6.0, 2.0)
    assert 4 <= len(window) <= 5
    assert window[0].data >= bytes([3])
    assert window[-1].data <= bytes([7])


def test_parse_annke_query() -> None:
    start, end = parse_playback_query_string(
        "starttime=2026-05-23T21%3A05%3A11Z&endtime=2026-05-23T21%3A05%3A41Z",
        profile=None,
    )
    assert start.year == 2026
    assert (end - start).total_seconds() == 30.0


def test_playback_query_from_request_split_params() -> None:
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/internal/rtsp-playback",
        "query_string": (
            b"mtx_path=Streaming/tracks/stream1"
            b"&starttime=2026-05-23T22%3A55%3A14"
            b"&endtime=2026-05-23T22%3A55%3A50"
        ),
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    q = playback_query_from_request(request, "")
    start, end = parse_playback_query_string(q, profile=None)
    assert start.hour == 22
    assert (end - start).total_seconds() == 36.0
