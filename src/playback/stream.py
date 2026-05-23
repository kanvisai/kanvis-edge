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
from src.ingestion.packet_decode import to_annex_b, trim_packets_from_keyframe
from src.brands import load_brand_profile
from src.brands.registry import default_brands_dir
from src.playback.window import PlaybackPlan, plan_playback_window

logger = logging.getLogger(__name__)


class PlaybackStreamError(Exception):
    """Error recuperable en playback RTSP."""


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

    if plan.needs_buffer:
        packets = trim_packets_from_keyframe(
            buffer.snapshot_between_ages(
                plan.buffer_start_sec_ago,
                plan.buffer_end_sec_ago,
            )
        )
        if not packets:
            raise PlaybackStreamError(
                f"Búfer insuficiente para {plan.buffer_start_sec_ago:.1f}s; "
                "activa ingesta y espera a que se llene"
            )
        logger.info(
            "Playback %s: búfer %.1fs (%.1f→%.1fs atrás), %d paquetes",
            camera.camera_id,
            plan.buffer_start_sec_ago - plan.buffer_end_sec_ago,
            plan.buffer_start_sec_ago,
            plan.buffer_end_sec_ago,
            len(packets),
        )
        async for chunk in _emit_packets_realtime(packets, codec, frame_interval):
            yield chunk

    if plan.needs_live_tail:
        logger.info(
            "Playback %s: cola en vivo %.1fs hasta %s",
            camera.camera_id,
            plan.live_tail_sec,
            plan.end.isoformat(),
        )
        deadline = time.monotonic() + plan.live_tail_sec
        async for chunk in _emit_live_until(consumer, codec, frame_interval, deadline):
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


async def _emit_packets_realtime(
    packets: list[RawPacket],
    codec: str,
    frame_interval: float,
) -> AsyncIterator[bytes]:
    for i, packet in enumerate(packets):
        yield to_annex_b(packet.data, codec)
        if i + 1 < len(packets):
            await asyncio.sleep(frame_interval)


async def _emit_live_until(
    consumer: StreamConsumer,
    codec: str,
    frame_interval: float,
    deadline_mono: float,
) -> AsyncIterator[bytes]:
    while time.monotonic() < deadline_mono:
        packet = await consumer.get_live_packet(timeout=0.5)
        if packet is None:
            await asyncio.sleep(0.01)
            continue
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
