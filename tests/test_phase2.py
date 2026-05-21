"""Tests Fase 2: WebRTC config y bridge."""

from __future__ import annotations

import asyncio

import pytest

from src.discovery.models import (
    CameraBufferSettings,
    CameraOutput,
    CameraRecord,
    CameraSource,
    CameraWebRTCOutput,
    OutputProtocol,
)
from src.ingestion.bridge import PacketBridge
from src.ingestion.buffer import PacketCircularBuffer, RawPacket
from src.webrtc.publisher import WebRtcMode, webrtc_mode


def _camera(webrtc: CameraWebRTCOutput) -> CameraRecord:
    return CameraRecord(
        camera_id="w1",
        source=CameraSource(host="10.0.0.1", fps=20),
        output=CameraOutput(protocol=OutputProtocol.WEBRTC, webrtc=webrtc),
        buffer=CameraBufferSettings(),
    )


def test_webrtc_modes() -> None:
    whep = CameraWebRTCOutput(enabled=True, mode="whep")
    whip = CameraWebRTCOutput(enabled=True, mode="whip", signaling_url="https://x/whip")
    assert webrtc_mode(whep) == WebRtcMode.WHEP
    assert webrtc_mode(whip) == WebRtcMode.WHIP


def test_packet_bridge_fanout() -> None:
    bridge = PacketBridge(max_queue_size=10)
    q1 = bridge.subscribe()
    q2 = bridge.subscribe()
    pkt = RawPacket(data=b"\x00\x00\x01", pts=0, dts=0, is_keyframe=True)
    bridge.publish(pkt)

    async def _read() -> int:
        p1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        p2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        return len(p1.data) + len(p2.data)

    total = asyncio.run(_read())
    assert total == 6


def test_webrtc_model_defaults() -> None:
    cam = _camera(CameraWebRTCOutput(enabled=True))
    assert cam.output.webrtc.rewind_offset_sec == 3.0
    assert "stun" in cam.output.webrtc.stun_urls[0]
