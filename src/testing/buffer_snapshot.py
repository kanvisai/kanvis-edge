"""Frame JPEG desde paquetes H.264/H.265 del búfer RAM (playback / prueba)."""

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


def _codec_candidates(video_codec: str | None) -> list[str]:
    raw = (video_codec or "").strip().lower()
    ordered: list[str] = []
    for name in (raw, "h264", "hevc", "h265", "mpeg4"):
        if name and name not in ordered:
            ordered.append(name)
    if "hevc" in ordered and "h265" not in ordered:
        ordered.append("h265")
    return ordered or ["h264", "hevc"]


def _open_decoder(codec_name: str) -> av.codec.context.CodecContext:
    name = codec_name
    if name == "h265":
        name = "hevc"
    try:
        return av.CodecContext.create(name, "r")
    except av.AVError:
        if name == "hevc":
            return av.CodecContext.create("h264", "r")
        raise


def capture_jpeg_from_buffer(
    buffer: PacketCircularBuffer,
    offset_sec: float,
    video_codec: str | None = None,
) -> bytes:
    """
    Decodifica un frame cercano a (ahora - offset_sec) usando el búfer circular.
    Prueba H.264 y HEVC según el códec detectado en ingesta.
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

    last_errors: list[str] = []
    for codec_try in _codec_candidates(video_codec):
        decoder: av.codec.context.CodecContext | None = None
        last_frame: av.VideoFrame | None = None
        best_frame: av.VideoFrame | None = None
        best_delta = float("inf")

        for pkt in packets[start_idx:]:
            if decoder is None or pkt.is_keyframe:
                try:
                    decoder = _open_decoder(codec_try)
                except av.AVError as exc:
                    last_errors.append(f"{codec_try}: {exc}")
                    decoder = None
                    break
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
