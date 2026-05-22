"""Frame JPEG desde paquetes H.264 del búfer RAM (playback / prueba)."""

from __future__ import annotations

import time

import av

from src.ingestion.buffer import PacketCircularBuffer
from src.testing.snapshot import SnapshotError


def _frame_to_jpeg(frame: av.VideoFrame) -> bytes:
    enc = av.CodecContext.create("mjpeg", "w")
    for packet in enc.encode(frame):
        return bytes(packet)
    raise SnapshotError("No se pudo codificar JPEG")


def capture_jpeg_from_buffer(
    buffer: PacketCircularBuffer,
    offset_sec: float,
) -> bytes:
    """
    Decodifica un frame cercano a (ahora - offset_sec) usando el búfer circular.
    """
    if offset_sec <= 0:
        raise SnapshotError("offset_sec debe ser > 0")
    packets = buffer.snapshot_last_seconds(offset_sec)
    if not packets:
        raise SnapshotError(
            f"Búfer vacío o insuficiente; espera al menos {offset_sec:.0f}s con broadcast activo"
        )
    target = time.monotonic() - offset_sec
    start_idx = 0
    for i, pkt in enumerate(packets):
        if pkt.is_keyframe:
            start_idx = i

    decoder: av.codec.context.CodecContext | None = None
    last_frame: av.VideoFrame | None = None
    best_frame: av.VideoFrame | None = None
    best_delta = float("inf")

    for pkt in packets[start_idx:]:
        if decoder is None or pkt.is_keyframe:
            decoder = av.CodecContext.create("h264", "r")
        try:
            decoded = list(decoder.decode(av.Packet(pkt.data)))
        except av.AVError:
            continue
        for frame in decoded:
            last_frame = frame
            delta = abs(pkt.captured_at - target)
            if delta < best_delta:
                best_delta = delta
                best_frame = frame

    chosen = best_frame or last_frame
    if chosen is None:
        raise SnapshotError(
            f"No hay frame decodable en los últimos {offset_sec:.0f}s del búfer"
        )
    return _frame_to_jpeg(chosen)
