"""VideoDispatcher: playback por offset y clip de evento nube."""

from __future__ import annotations

import asyncio
import struct
import time
from typing import AsyncIterator

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord
from src.ingestion.buffer import RawPacket
from src.ingestion.consumer import StreamConsumer, StreamConsumerManager


def _frame_packet(packet: RawPacket) -> bytes:
    """Encapsulado binario: uint32 length + payload (H.264/H.265 raw)."""
    return struct.pack(">I", len(packet.data)) + packet.data


class VideoDispatcher:
    """Despacho de vídeo desde búfer RAM hacia la nube o pruebas."""

    def __init__(
        self,
        settings: AppSettings,
        consumer_manager: StreamConsumerManager,
    ) -> None:
        self._settings = settings
        self._manager = consumer_manager

    def _resolve_camera(self, camera_id: str) -> tuple[StreamConsumer, CameraRecord]:
        consumer = self._manager.get_consumer(camera_id)
        if consumer is None:
            raise KeyError(f"Cámara no activa o no encontrada: {camera_id}")
        camera = self._manager.get_camera_record(camera_id)
        if camera is None:
            raise KeyError(f"Sin metadatos de cámara: {camera_id}")
        return consumer, camera

    async def stream_playback(
        self,
        camera_id: str,
        offset_sec: float,
        duration_sec: float | None = None,
        include_live_tail: bool = False,
    ) -> AsyncIterator[bytes]:
        """
        Playback desde (ahora - offset_sec).
        Si duration_sec está definido, limita la ventana histórica.
        include_live_tail: sigue emitiendo en vivo tras el histórico (pruebas).
        """
        consumer, camera = self._resolve_camera(camera_id)
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            raise KeyError(f"Sin búfer para cámara: {camera_id}")

        max_offset = camera.buffer.duration_seconds
        if offset_sec > max_offset:
            raise ValueError(
                f"offset_sec ({offset_sec}) supera el búfer ({max_offset}s)"
            )

        window = duration_sec if duration_sec is not None else offset_sec
        window = min(window, offset_sec)
        past_packets = buffer.snapshot_last_seconds(offset_sec)
        if duration_sec is not None and past_packets:
            oldest_allowed = past_packets[-1].captured_at - duration_sec
            past_packets = [p for p in past_packets if p.captured_at >= oldest_allowed]

        yield b"KANV1\x00"
        yield struct.pack(">H", 1)  # versión payload: 1 = playback
        yield struct.pack(">f", offset_sec)

        for packet in past_packets:
            yield _frame_packet(packet)

        if include_live_tail and duration_sec is not None:
            deadline = time.monotonic() + duration_sec
            while time.monotonic() < deadline:
                packet = await consumer.get_live_packet(timeout=0.5)
                if packet is not None:
                    yield _frame_packet(packet)
                else:
                    await asyncio.sleep(0.01)

    async def stream_event_clip(
        self,
        camera_id: str,
        pre_seconds: float | None = None,
        post_seconds: float | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Clip de evento: pre-alarma desde búfer + post en vivo.
        No vacía el búfer (permite múltiples peticiones y playback paralelo).
        """
        consumer, camera = self._resolve_camera(camera_id)
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            raise KeyError(f"Sin búfer para cámara: {camera_id}")

        pre = pre_seconds if pre_seconds is not None else camera.buffer.event_pre_seconds
        post = post_seconds if post_seconds is not None else camera.buffer.event_post_seconds

        past_packets = buffer.snapshot_last_seconds(pre)
        deadline = time.monotonic() + post

        yield b"KANV1\x00"
        yield struct.pack(">H", 2)  # versión payload: 2 = event clip

        for packet in past_packets:
            yield _frame_packet(packet)

        while time.monotonic() < deadline:
            packet = await consumer.get_live_packet(timeout=0.5)
            if packet is not None:
                yield _frame_packet(packet)
            else:
                await asyncio.sleep(0.01)
