"""Frame JPEG desde paquetes H.264/H.265 del búfer RAM (playback / prueba)."""

from __future__ import annotations

import time

import av

from src.ingestion.buffer import PacketCircularBuffer
from src.ingestion.packet_decode import decode_packet, trim_packets_from_keyframe
from src.testing.snapshot import SnapshotError


def _codec_candidates(video_codec: str | None) -> list[str]:
    raw = (video_codec or "").strip().lower()
    ordered: list[str] = []
    for name in (raw, "h264", "hevc", "h265", "mpeg4"):
        if name and name not in ordered:
            ordered.append(name)
    if "hevc" in ordered and "h265" not in ordered:
        ordered.append("h265")
    return ordered or ["h264", "hevc"]


def _frame_to_jpeg(frame: av.VideoFrame) -> bytes:
    enc = av.CodecContext.create("mjpeg", "w")
    for packet in enc.encode(frame):
        return bytes(packet)
    raise SnapshotError("No se pudo codificar JPEG")


def capture_jpeg_from_buffer(
    buffer: PacketCircularBuffer,
    offset_sec: float,
    video_codec: str | None = None,
    video_extradata: bytes | None = None,
) -> bytes:
    """
    Decodifica un frame cercano a (ahora - offset_sec) usando el búfer circular.
    Prueba H.264 y HEVC según el códec detectado en ingesta.
    """
    if offset_sec <= 0:
        raise SnapshotError("offset_sec debe ser > 0")
    packets = trim_packets_from_keyframe(buffer.snapshot_last_seconds(offset_sec))
    if not packets:
        raise SnapshotError(
            f"Búfer vacío o insuficiente; espera al menos {offset_sec:.0f}s con broadcast activo"
        )
    target = time.monotonic() - offset_sec
    last_errors: list[str] = []

    for codec_try in _codec_candidates(video_codec):
        decoder = None
        active: str | None = None
        last_frame: av.VideoFrame | None = None
        best_frame: av.VideoFrame | None = None
        best_delta = float("inf")

        for pkt in packets:
            try:
                frames, decoder, active = decode_packet(
                    pkt,
                    decoder,
                    active,
                    [codec_try],
                    video_extradata,
                )
            except av.AVError as exc:
                last_errors.append(f"{codec_try}: {exc}")
                decoder = None
                active = None
                continue
            for frame in frames:
                last_frame = frame
                delta = abs(pkt.captured_at - target)
                if delta < best_delta:
                    best_delta = delta
                    best_frame = frame

        chosen = best_frame or last_frame
        if chosen is not None:
            try:
                return _frame_to_jpeg(chosen)
            except SnapshotError:
                raise
            except Exception as exc:
                raise SnapshotError(f"JPEG: {exc}") from exc

    codecs = ", ".join(_codec_candidates(video_codec))
    err_tail = "; ".join(last_errors[:3])
    detail = f" ({err_tail})" if err_tail else ""
    raise SnapshotError(
        f"No hay frame decodable en los últimos {offset_sec:.0f}s del búfer "
        f"(probadores: {codecs}){detail}"
    )
