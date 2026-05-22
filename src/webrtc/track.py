"""Video track WebRTC alimentado desde el búfer H.264/HEVC de ingesta."""

from __future__ import annotations

import asyncio
import logging
from fractions import Fraction

import av
from aiortc import MediaStreamTrack

from src.ingestion.buffer import PacketCircularBuffer, RawPacket

logger = logging.getLogger(__name__)


def _codec_try_order(video_codec: str | None) -> list[str]:
    raw = (video_codec or "").strip().lower()
    ordered: list[str] = []
    for name in (raw, "h264", "hevc", "h265", "mpeg4"):
        if not name:
            continue
        if name == "h265":
            name = "hevc"
        if name not in ordered:
            ordered.append(name)
    return ordered or ["h264", "hevc"]


class H264PacketVideoTrack(MediaStreamTrack):
    """
    Decodifica paquetes comprimidos del búfer para WebRTC.
    Soporta H.264 y HEVC según ingesta o detección en keyframe.
    """

    kind = "video"

    def __init__(
        self,
        live_queue: asyncio.Queue[RawPacket],
        buffer: PacketCircularBuffer,
        target_fps: int = 20,
        video_codec: str | None = None,
    ) -> None:
        super().__init__()
        self._live_queue = live_queue
        self._buffer = buffer
        self._target_fps = max(1, target_fps)
        self._frame_interval = 1.0 / self._target_fps
        self._codec_order = _codec_try_order(video_codec)
        self._decoder: av.codec.context.CodecContext | None = None
        self._active_codec: str | None = None
        self._rewind_packets: list[RawPacket] = []
        self._rewind_index = 0
        self._pts = 0
        self._time_base = Fraction(1, 90000)
        self._closed = False
        self._decode_failures = 0
        self._frames_out = 0

    async def prime_from_buffer(self, offset_sec: float | None = None) -> int:
        """Carga el búfer en cola de rewind para que haya imagen al conectar."""
        offset = offset_sec if offset_sec is not None else 3.0
        span = self._buffer.span_seconds()
        if span < 0.35:
            return 0
        use = min(offset, span * 0.9)
        return await self.rewind(use)

    async def rewind(self, offset_sec: float) -> int:
        """Reproduce paquetes desde (ahora - offset_sec) antes de volver al vivo."""
        packets = self._buffer.snapshot_last_seconds(offset_sec)
        self._rewind_packets = packets
        self._rewind_index = 0
        self._decoder = None
        self._active_codec = None
        logger.info("WebRTC rewind: %d paquetes (%.1fs)", len(packets), offset_sec)
        return len(packets)

    async def recv(self) -> av.VideoFrame:
        if self._closed:
            raise Exception("Track cerrado")

        idle_loops = 0
        while True:
            raw = await self._next_packet()
            frames = self._decode_packet(raw)
            if frames:
                frame = frames[0]
                frame.pts = self._pts
                frame.time_base = self._time_base
                self._pts += int(90000 / self._target_fps)
                self._frames_out += 1
                self._decode_failures = 0
                await asyncio.sleep(self._frame_interval * 0.25)
                return frame
            idle_loops += 1
            if idle_loops % 50 == 0:
                logger.debug(
                    "WebRTC esperando frame decodable (fallos=%s, rewind=%s/%s)",
                    self._decode_failures,
                    self._rewind_index,
                    len(self._rewind_packets),
                )
            await asyncio.sleep(0.02)

    async def _next_packet(self) -> RawPacket:
        if self._rewind_index < len(self._rewind_packets):
            packet = self._rewind_packets[self._rewind_index]
            self._rewind_index += 1
            return packet
        while True:
            try:
                return await asyncio.wait_for(self._live_queue.get(), timeout=3.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(0.05)

    def _open_decoder(self, codec_name: str) -> av.codec.Context | None:
        name = codec_name
        if name == "h265":
            name = "hevc"
        try:
            return av.CodecContext.create(name, "r")
        except av.AVError:
            return None

    def _decode_packet(self, raw: RawPacket) -> list[av.VideoFrame]:
        if raw.is_keyframe:
            self._decoder = None
            self._active_codec = None

        codecs = list(self._codec_order)
        if self._active_codec and self._active_codec in codecs:
            codecs = [self._active_codec] + [c for c in codecs if c != self._active_codec]

        for codec_name in codecs:
            if self._decoder is None or self._active_codec != codec_name:
                self._decoder = self._open_decoder(codec_name)
                if self._decoder is None:
                    continue
                self._active_codec = codec_name
            try:
                pkt = av.Packet(raw.data)
                frames = list(self._decoder.decode(pkt))
                if frames:
                    return frames
            except av.AVError:
                self._decoder = None
                self._active_codec = None
                continue
            except Exception:
                logger.debug("Decode WebRTC falló (%s)", codec_name, exc_info=True)
                self._decoder = None
                self._active_codec = None

        self._decode_failures += 1
        return []

    @property
    def rewind_pending(self) -> int:
        return max(0, len(self._rewind_packets) - self._rewind_index)

    @property
    def frames_sent(self) -> int:
        return self._frames_out

    def stop(self) -> None:
        self._closed = True
        super().stop()
