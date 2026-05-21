"""Video track WebRTC alimentado desde el búfer H.264 de ingesta."""

from __future__ import annotations

import asyncio
import logging
from fractions import Fraction

import av
from aiortc import MediaStreamTrack

from src.ingestion.buffer import PacketCircularBuffer, RawPacket

logger = logging.getLogger(__name__)


class H264PacketVideoTrack(MediaStreamTrack):
    """
    Decodifica paquetes H.264 del búfer para WebRTC (solo en salida WebRTC;
    la ingesta sigue sin decodificar a RGB en el búfer).
    """

    kind = "video"

    def __init__(
        self,
        live_queue: asyncio.Queue[RawPacket],
        buffer: PacketCircularBuffer,
        target_fps: int = 20,
    ) -> None:
        super().__init__()
        self._live_queue = live_queue
        self._buffer = buffer
        self._target_fps = max(1, target_fps)
        self._frame_interval = 1.0 / self._target_fps
        self._decoder: av.codec.context.CodecContext | None = None
        self._rewind_packets: list[RawPacket] = []
        self._rewind_index = 0
        self._pts = 0
        self._time_base = Fraction(1, 90000)
        self._closed = False

    async def rewind(self, offset_sec: float) -> int:
        """Reproduce paquetes desde (ahora - offset_sec) antes de volver al vivo."""
        packets = self._buffer.snapshot_last_seconds(offset_sec)
        self._rewind_packets = packets
        self._rewind_index = 0
        if self._decoder is not None:
            self._decoder = None
        logger.info("WebRTC rewind: %d paquetes (%.1fs)", len(packets), offset_sec)
        return len(packets)

    async def recv(self) -> av.VideoFrame:
        if self._closed:
            raise Exception("Track cerrado")

        while True:
            raw = await self._next_packet()
            frames = self._decode_packet(raw)
            if frames:
                frame = frames[0]
                frame.pts = self._pts
                frame.time_base = self._time_base
                self._pts += int(90000 / self._target_fps)
                await asyncio.sleep(self._frame_interval * 0.5)
                return frame

    async def _next_packet(self) -> RawPacket:
        if self._rewind_index < len(self._rewind_packets):
            packet = self._rewind_packets[self._rewind_index]
            self._rewind_index += 1
            return packet
        return await asyncio.wait_for(self._live_queue.get(), timeout=5.0)

    def _decode_packet(self, raw: RawPacket) -> list[av.VideoFrame]:
        try:
            if self._decoder is None or raw.is_keyframe:
                self._decoder = av.CodecContext.create("h264", "r")
            pkt = av.Packet(raw.data)
            return list(self._decoder.decode(pkt))
        except av.AVError:
            return []
        except Exception:
            logger.debug("Decode WebRTC falló", exc_info=True)
            return []

    @property
    def rewind_pending(self) -> int:
        return max(0, len(self._rewind_packets) - self._rewind_index)

    def stop(self) -> None:
        self._closed = True
        super().stop()
