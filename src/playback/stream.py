"""Emisión H.264 Annex-B en tiempo real: búfer → vivo → grabación cámara."""

from __future__ import annotations

import asyncio
import logging
import queue as sync_queue
import threading
import time
from typing import AsyncIterator

import av

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord
from src.ingestion.buffer import PacketCircularBuffer, RawPacket
from src.ingestion.consumer import StreamConsumer
from src.ingestion.packet_decode import (
    h264_avcc_extradata_from_keyframe,
    iter_annex_b_nals,
    to_annex_b,
    trim_packets_from_keyframe,
)
from src.brands import load_brand_profile
from src.brands.registry import default_brands_dir
from src.playback.window import PlaybackPlan, plan_playback_window

logger = logging.getLogger(__name__)

_LIVE_STALE_MAX_SEC = 2.0

_ANNEX_B_START = b"\x00\x00\x00\x01"


class PlaybackStreamError(Exception):
    """Error recuperable en playback RTSP."""


def _extradata_to_annex_b(extradata: bytes, codec: str) -> bytes:
    """Convierte extradata (avcC/hvcC) a NALUs SPS/PPS Annex-B para iniciar stream."""
    from src.ingestion.packet_decode import is_annex_b, avcc_to_annex_b

    if is_annex_b(extradata):
        return extradata
    if codec in ("h264", "avc", "avc1"):
        return _parse_avcc_extradata(extradata)
    return avcc_to_annex_b(extradata)


def _parse_avcc_extradata(data: bytes) -> bytes:
    """Extrae SPS/PPS de avcC extradata y devuelve Annex-B."""
    out = bytearray()
    try:
        if len(data) < 7 or data[0] != 0x01:
            return data
        i = 5
        num_sps = data[i] & 0x1F
        i += 1
        for _ in range(num_sps):
            sps_len = int.from_bytes(data[i : i + 2], "big")
            i += 2
            out.extend(_ANNEX_B_START)
            out.extend(data[i : i + sps_len])
            i += sps_len
        num_pps = data[i]
        i += 1
        for _ in range(num_pps):
            pps_len = int.from_bytes(data[i : i + 2], "big")
            i += 2
            out.extend(_ANNEX_B_START)
            out.extend(data[i : i + pps_len])
            i += pps_len
    except (IndexError, ValueError):
        return data
    return bytes(out) if out else data


def _extract_sps_pps_from_keyframe(keyframe_data: bytes, codec: str) -> bytes | None:
    """Extrae SPS/PPS directamente del primer keyframe Annex-B como fallback."""
    annex = to_annex_b(keyframe_data, codec)
    parts = bytearray()
    for nal in iter_annex_b_nals(annex):
        if not nal:
            continue
        if codec in ("h264", "avc", "avc1"):
            ntype = nal[0] & 0x1F
            if ntype in (7, 8):  # SPS=7, PPS=8
                parts.extend(_ANNEX_B_START)
                parts.extend(nal)
        else:
            ntype = (nal[0] >> 1) & 0x3F
            if ntype in (32, 33, 34):  # VPS=32, SPS=33, PPS=34 (HEVC)
                parts.extend(_ANNEX_B_START)
                parts.extend(nal)
    return bytes(parts) if parts else None


def _ensure_sps_pps(
    extradata: bytes | None,
    packets: list[RawPacket],
    codec: str,
) -> bytes | None:
    """
    Obtiene SPS/PPS Annex-B garantizado, con cascada de fuentes:
    1. extradata del codec context (consumer)
    2. NALs SPS/PPS dentro del primer keyframe del buffer
    3. Reconstrucción avcC desde el keyframe
    """
    if extradata:
        result = _extradata_to_annex_b(extradata, codec)
        if result and len(result) > 8:
            return result

    for pkt in packets:
        if pkt.is_keyframe:
            inline = _extract_sps_pps_from_keyframe(pkt.data, codec)
            if inline and len(inline) > 8:
                return inline
            if codec in ("h264", "avc", "avc1"):
                avcc = h264_avcc_extradata_from_keyframe(pkt.data)
                if avcc:
                    result = _extradata_to_annex_b(avcc, codec)
                    if result and len(result) > 8:
                        return result
            break

    return None


async def stream_playback_h264(
    *,
    camera: CameraRecord,
    consumer: StreamConsumer,
    buffer: PacketCircularBuffer,
    settings: AppSettings,
    start,
    end,
) -> AsyncIterator[bytes]:
    """
    Genera Annex-B H.264/HEVC según ventana starttime/endtime.

    Orden: tramo en búfer RAM, cola en vivo hasta endtime, playback de cámara
    para histórico anterior al búfer.

    El stream siempre comienza con SPS/PPS + IDR para garantizar
    que el decoder puede inicializarse desde el primer frame.
    """
    depth = camera.effective_buffer_duration(settings.buffer_duration_seconds)
    plan = plan_playback_window(
        start=start,
        end=end,
        buffer_depth_sec=depth,
    )
    codec = consumer.metrics.snapshot().get("video_codec") or "h264"
    fps = max(1, camera.source.fps)
    frame_interval = 1.0 / fps

    extradata = consumer.video_extradata
    sps_pps_emitted = False
    last_buffer_ts: float = 0.0

    if plan.needs_buffer:
        raw_snap = buffer.snapshot_between_ages(
            plan.buffer_start_sec_ago,
            plan.buffer_end_sec_ago,
        )
        packets = trim_packets_from_keyframe(raw_snap)
        if not packets:
            raise PlaybackStreamError(
                f"Búfer insuficiente para {plan.buffer_start_sec_ago:.1f}s; "
                "activa ingesta y espera a que se llene"
            )
        skipped = len(raw_snap) - len(packets)
        logger.info(
            "Playback %s: búfer %.1fs (%.1f→%.1fs atrás), %d paquetes "
            "(descartados %d pre-keyframe, burst mode)",
            camera.camera_id,
            plan.buffer_start_sec_ago - plan.buffer_end_sec_ago,
            plan.buffer_start_sec_ago,
            plan.buffer_end_sec_ago,
            len(packets),
            skipped,
        )
        sps_pps = _ensure_sps_pps(extradata, packets, codec)
        if sps_pps:
            yield sps_pps
            sps_pps_emitted = True
        last_buffer_ts = packets[-1].captured_at
        async for chunk in _emit_buffer_burst(packets, codec):
            yield chunk

    if plan.needs_live_tail:
        if not sps_pps_emitted:
            sps_pps = _ensure_sps_pps(extradata, [], codec)
            if sps_pps:
                yield sps_pps
                sps_pps_emitted = True
        if last_buffer_ts > 0:
            cutoff = last_buffer_ts
        else:
            cutoff = time.monotonic() - 0.5
        dropped = await consumer.purge_live_queue_older_than(cutoff)
        if dropped:
            logger.info(
                "Playback %s: descartados %d paquetes vivos pre-búfer al entrar en live tail",
                camera.camera_id,
                dropped,
            )
        logger.info(
            "Playback %s: live tail %.1fs hasta %s",
            camera.camera_id,
            plan.live_tail_sec,
            plan.end.isoformat(),
        )
        deadline = time.monotonic() + plan.live_tail_sec
        async for chunk in _emit_live_until(
            consumer, codec, frame_interval, deadline, not sps_pps_emitted
        ):
            yield chunk

    device_playback = True
    brand = (camera.source.brand or "").strip().lower()
    if brand:
        try:
            profile = load_brand_profile(brand, default_brands_dir(settings.config_dir))
            device_playback = profile.protocols.rtsp.device_playback_supported
        except FileNotFoundError:
            pass

    if plan.needs_camera and device_playback:
        assert plan.camera_start is not None and plan.camera_end is not None
        logger.info(
            "Playback %s: cámara %s → %s",
            camera.camera_id,
            plan.camera_start.isoformat(),
            plan.camera_end.isoformat(),
        )
        async for chunk in _stream_camera_playback(
            camera,
            settings,
            plan.camera_start,
            plan.camera_end,
            codec,
            frame_interval,
        ):
            yield chunk
    elif plan.needs_camera and not device_playback:
        logger.info(
            "Playback %s: histórico omitido (marca sin playback en dispositivo)",
            camera.camera_id,
        )


async def _emit_buffer_burst(
    packets: list[RawPacket],
    codec: str,
) -> AsyncIterator[bytes]:
    """
    Emite paquetes del buffer lo más rápido posible (sin pacing real-time).

    El burst minimiza el gap con el live tail: si pacemos a velocidad real,
    durante la emisión se acumulan paquetes vivos que luego se pierden.
    FFmpeg (-f h264) asigna PTS secuenciales por frame, así que el stream
    RTSP resultante tiene PTS continuos independientemente de la velocidad
    de llegada.
    """
    for i, packet in enumerate(packets):
        yield to_annex_b(packet.data, codec)
        if (i + 1) % 60 == 0:
            await asyncio.sleep(0)


async def _emit_live_until(
    consumer: StreamConsumer,
    codec: str,
    frame_interval: float,
    deadline_mono: float,
    wait_for_keyframe: bool = False,
) -> AsyncIterator[bytes]:
    waiting_kf = wait_for_keyframe
    while time.monotonic() < deadline_mono:
        packet = await consumer.get_live_packet(timeout=0.5)
        if packet is None:
            await asyncio.sleep(0.01)
            continue
        if waiting_kf:
            if not packet.is_keyframe:
                continue
            waiting_kf = False
        yield to_annex_b(packet.data, codec)
        await asyncio.sleep(frame_interval * 0.85)


async def _stream_camera_playback(
    camera: CameraRecord,
    settings: AppSettings,
    start,
    end,
    codec: str,
    frame_interval: float,
) -> AsyncIterator[bytes]:
    url = camera.rtsp_playback_url(
        starttime=start,
        endtime=end,
        settings=settings,
        target="device",
    )
    out_q: sync_queue.Queue[bytes | None] = sync_queue.Queue(maxsize=500)
    stop = threading.Event()

    def _read() -> None:
        container = None
        try:
            container = av.open(
                url,
                options={
                    "rtsp_transport": camera.source.transport or "tcp",
                    "stimeout": "10000000",
                },
            )
            stream = container.streams.video[0]
            for packet in container.demux(stream):
                if stop.is_set():
                    break
                if packet.size == 0:
                    continue
                out_q.put(to_annex_b(bytes(packet), codec))
        except Exception as exc:
            logger.warning("Playback cámara %s: %s", camera.camera_id, exc)
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            out_q.put(None)

    thread = threading.Thread(target=_read, name=f"pb-cam-{camera.camera_id}", daemon=True)
    thread.start()
    try:
        while True:
            chunk = await asyncio.to_thread(out_q.get)
            if chunk is None:
                break
            yield chunk
            await asyncio.sleep(frame_interval * 0.85)
    finally:
        stop.set()
        thread.join(timeout=2.0)
