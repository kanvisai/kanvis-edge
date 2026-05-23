"""Tests cola en vivo del StreamConsumer (playback)."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.config_loader import AppSettings
from src.discovery.models import (
    CameraBufferSettings,
    CameraOutput,
    CameraRecord,
    CameraSource,
)
from src.ingestion.buffer import RawPacket
from src.ingestion.consumer import StreamConsumer


def _consumer() -> StreamConsumer:
    cam = CameraRecord(
        camera_id="cam-test",
        source=CameraSource(host="127.0.0.1"),
        output=CameraOutput(),
        buffer=CameraBufferSettings(),
    )
    from src.ingestion.buffer import PacketCircularBuffer

    return StreamConsumer(cam, PacketCircularBuffer(60.0), AppSettings())


@pytest.mark.asyncio
async def test_purge_live_queue_drops_stale() -> None:
    consumer = _consumer()
    loop = asyncio.get_running_loop()
    consumer.bind_async_loop(loop)
    assert consumer._live_queue is not None
    now = time.monotonic()
    old = RawPacket(data=b"old", pts=0, dts=0, is_keyframe=True, captured_at=now - 120)
    fresh = RawPacket(data=b"new", pts=1, dts=1, is_keyframe=True, captured_at=now - 0.2)
    await consumer._live_queue.put(old)
    await consumer._live_queue.put(fresh)
    dropped = await consumer.purge_live_queue_older_than(now - 0.5)
    assert dropped == 1
    remaining = await consumer.get_live_packet(timeout=0.1)
    assert remaining is not None
    assert remaining.data == b"new"
