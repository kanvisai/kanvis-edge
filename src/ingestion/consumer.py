"""StreamConsumer: ingesta RTSP con PyAV sin decodificar a píxeles."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING

import av

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, ExternalAccessMode
from src.ingestion.bridge import PacketBridge
from src.ingestion.buffer import PacketCircularBuffer, RawPacket
from src.ingestion.metrics import IngestMetrics

if TYPE_CHECKING:
    from src.discovery.repository import CameraRepository
    from src.schedule.service import OperatingScheduleService

logger = logging.getLogger(__name__)


class StreamConsumer:
    """
    Consumidor dedicado por cámara: conecta al Main Stream Live,
    demux con codec copy (solo paquetes de vídeo comprimidos).
    """

    def __init__(
        self,
        camera: CameraRecord,
        buffer: PacketCircularBuffer,
        settings: AppSettings,
    ) -> None:
        self._camera = camera
        self._buffer = buffer
        self._settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._live_queue: asyncio.Queue[RawPacket] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.metrics = IngestMetrics()
        self.packet_bridge = PacketBridge()

    @property
    def camera_id(self) -> str:
        return self._camera.camera_id

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def video_extradata(self) -> bytes | None:
        return self.metrics.get_video_extradata()

    def bind_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._live_queue = asyncio.Queue(maxsize=5000)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"stream-{self._camera.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("StreamConsumer iniciado: %s", self._camera.camera_id)

    def stop(self) -> bool:
        """Detiene el hilo de ingesta. False si el hilo no terminó a tiempo."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                logger.warning(
                    "StreamConsumer %s: hilo RTSP no terminó en 10s (conexión colgada)",
                    self._camera.camera_id,
                )
                return False
            self._thread = None
        logger.info("StreamConsumer detenido: %s", self._camera.camera_id)
        return True

    def is_ingest_stale(self, max_idle_sec: float) -> bool:
        """True si no llegan paquetes (conexión zombie)."""
        snap = self.metrics.snapshot()
        if not snap.get("connected"):
            return True
        idle = snap.get("last_packet_idle_sec")
        if idle is None:
            return True
        return float(idle) > max_idle_sec

    def _push_live(self, packet: RawPacket) -> None:
        if self._loop is None or self._live_queue is None:
            return

        def _put() -> None:
            try:
                self._live_queue.put_nowait(packet)
            except asyncio.QueueFull:
                try:
                    self._live_queue.get_nowait()
                    self._live_queue.put_nowait(packet)
                except asyncio.QueueEmpty:
                    pass

        self._loop.call_soon_threadsafe(_put)

    def _run(self) -> None:
        delay = self._settings.reconnect_base_delay
        url = self._camera.rtsp_url()

        while not self._stop.is_set():
            container = None
            try:
                container = av.open(
                    url,
                    options={
                        "rtsp_transport": "tcp",
                        "stimeout": "5000000",
                        # Evita lecturas RTSP colgadas indefinidamente (causa habitual
                        # de playback roto tras horas sin reiniciar el servicio).
                        "rw_timeout": "10000000",
                        "fflags": "nobuffer",
                        "flags": "low_delay",
                    },
                )
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                codec_name = getattr(stream.codec, "name", None) or ""
                if codec_name:
                    self.metrics.set_video_codec(codec_name)
                ctx = stream.codec_context
                extra = ctx.extradata
                if extra:
                    self.metrics.set_video_extradata(bytes(extra))

                delay = self._settings.reconnect_base_delay
                self.metrics.on_connected()
                logger.info("Conectado RTSP: %s", self._camera.camera_id)

                for packet in container.demux(stream):
                    if self._stop.is_set():
                        break
                    if packet.size == 0:
                        continue
                    self.metrics.on_packet(packet.size)
                    raw = RawPacket(
                        data=bytes(packet),
                        pts=packet.pts,
                        dts=packet.dts,
                        is_keyframe=packet.is_keyframe,
                        time_base_num=stream.time_base.numerator,
                        time_base_den=stream.time_base.denominator,
                    )
                    self._buffer.append(raw)
                    self.packet_bridge.publish(raw)
                    self._push_live(raw)

            except av.AVError as exc:
                self.metrics.on_error(str(exc))
                logger.warning(
                    "RTSP error %s: %s — reintento en %.1fs",
                    self._camera.camera_id,
                    exc,
                    delay,
                )
            except Exception as exc:
                self.metrics.on_error(str(exc))
                logger.exception("Error inesperado en %s", self._camera.camera_id)
            finally:
                self.metrics.on_disconnected()
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        pass

            if self._stop.is_set():
                break
            self._stop.wait(delay)
            delay = min(delay * 2, self._settings.reconnect_max_delay)

    async def get_live_packet(self, timeout: float = 1.0) -> RawPacket | None:
        if self._live_queue is None:
            return None
        try:
            return await asyncio.wait_for(self._live_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def purge_live_queue_older_than(self, cutoff_mono: float) -> int:
        """Quita paquetes en cola más antiguos que cutoff_mono (time.monotonic)."""
        if self._live_queue is None:
            return 0
        kept: list[RawPacket] = []
        dropped = 0
        while True:
            try:
                pkt = self._live_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pkt.captured_at >= cutoff_mono:
                kept.append(pkt)
            else:
                dropped += 1
        for pkt in kept:
            try:
                self._live_queue.put_nowait(pkt)
            except asyncio.QueueFull:
                break
        return dropped

    async def trim_live_queue(self, keep_sec: float) -> int:
        """Descarta paquetes en cola viva más antiguos que keep_sec."""
        if keep_sec <= 0:
            return 0
        return await self.purge_live_queue_older_than(time.monotonic() - keep_sec)


class StreamConsumerManager:
    """Gestiona consumidores por camera_id con hot-reload del inventario."""

    def __init__(
        self,
        settings: AppSettings,
        repository: CameraRepository,
        schedule: OperatingScheduleService | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._schedule = schedule
        self._consumers: dict[str, StreamConsumer] = {}
        self._buffers: dict[str, PacketCircularBuffer] = {}
        self._cameras: dict[str, CameraRecord] = {}
        self._broadcast_ingest_ids: set[str] = set()
        self._lock = asyncio.Lock()

    def is_broadcast_ingest_active(self, camera_id: str) -> bool:
        return camera_id in self._broadcast_ingest_ids

    async def set_broadcast_ingest(
        self,
        camera_id: str,
        active: bool,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Ingesta/búfer solo mientras el broadcast está activo (UI)."""
        async with self._lock:
            if active:
                self._broadcast_ingest_ids.add(camera_id)
            else:
                self._broadcast_ingest_ids.discard(camera_id)
                consumer = self._consumers.pop(camera_id, None)
                if consumer:
                    consumer.stop()
                self._buffers.pop(camera_id, None)
                self._cameras.pop(camera_id, None)
        await self.sync_from_repository(loop)

    def _stop_all_locked(self) -> None:
        for consumer in self._consumers.values():
            consumer.stop()
        self._consumers.clear()
        self._buffers.clear()
        self._cameras.clear()

    def get_buffer(self, camera_id: str) -> PacketCircularBuffer | None:
        return self._buffers.get(camera_id)

    def get_consumer(self, camera_id: str) -> StreamConsumer | None:
        return self._consumers.get(camera_id)

    def get_camera_record(self, camera_id: str) -> CameraRecord | None:
        return self._cameras.get(camera_id)

    def _buffer_for_camera(self, camera: CameraRecord) -> PacketCircularBuffer:
        duration = camera.effective_buffer_duration(
            self._settings.buffer_duration_seconds
        )
        return PacketCircularBuffer(
            max_duration_seconds=duration,
            max_packets_safety=self._settings.buffer_max_packets_safety,
        )

    async def sync_from_repository(self, loop: asyncio.AbstractEventLoop) -> None:
        """Añade/quita consumidores según inventario sin reiniciar el proceso."""
        if self._schedule is not None and not self._schedule.is_operating_now():
            async with self._lock:
                if self._consumers:
                    logger.info(
                        "Horario operativo inactivo — deteniendo ingesta y búfer"
                    )
                    self._stop_all_locked()
            return

        cameras = await self._repository.list_enabled()
        gateway_ingest_ids = {
            c.camera_id
            for c in cameras
            if c.output.gateway.enabled
            and c.output.gateway.access_mode != ExternalAccessMode.DIRECT
        }
        ingest_cameras = [
            c
            for c in cameras
            if c.camera_id in self._broadcast_ingest_ids
            or c.camera_id in gateway_ingest_ids
        ]
        active_ids = {c.camera_id for c in ingest_cameras}

        async with self._lock:
            for cam_id in list(self._consumers):
                if cam_id not in active_ids:
                    self._consumers[cam_id].stop()
                    del self._consumers[cam_id]
                    del self._buffers[cam_id]
                    del self._cameras[cam_id]

            for camera in ingest_cameras:
                duration = camera.effective_buffer_duration(
                    self._settings.buffer_duration_seconds
                )
                if camera.camera_id in self._consumers:
                    buf = self._buffers[camera.camera_id]
                    buf.set_max_duration(duration)
                    self._cameras[camera.camera_id] = camera
                    continue
                buf = self._buffer_for_camera(camera)
                consumer = StreamConsumer(camera, buf, self._settings)
                consumer.bind_async_loop(loop)
                consumer.start()
                self._buffers[camera.camera_id] = buf
                self._consumers[camera.camera_id] = consumer
                self._cameras[camera.camera_id] = camera

    async def run_inventory_watcher(self, loop: asyncio.AbstractEventLoop) -> None:
        """Poll del inventario cada 30s para mutabilidad sin reinicio."""
        while True:
            try:
                await self.sync_from_repository(loop)
            except Exception:
                logger.exception("Error sincronizando inventario de cámaras")
            await asyncio.sleep(30)

    async def maintain_ingest_health(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Recuperación tras horas en marcha: reinicia consumers sin paquetes
        y recorta colas vivas acumuladas.
        """
        max_idle = self._settings.ingest_stale_timeout_sec
        trim_sec = self._settings.live_queue_trim_sec
        stale_ids: list[str] = []

        async with self._lock:
            consumers = list(self._consumers.items())

        for cam_id, consumer in consumers:
            trimmed = await consumer.trim_live_queue(trim_sec)
            if trimmed > 0:
                logger.debug(
                    "Cola viva %s: recortados %d paquetes (>%ds)",
                    cam_id,
                    trimmed,
                    int(trim_sec),
                )
            if consumer.is_ingest_stale(max_idle):
                stale_ids.append(cam_id)

        for cam_id in stale_ids:
            await self._restart_consumer(cam_id, loop)

    async def _restart_consumer(
        self,
        camera_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        async with self._lock:
            consumer = self._consumers.get(camera_id)
            camera = self._cameras.get(camera_id)
            buf = self._buffers.get(camera_id)
            if consumer is None or camera is None or buf is None:
                return
            logger.warning(
                "Ingesta estancada en %s (sin paquetes >%.0fs); reiniciando consumer",
                camera_id,
                self._settings.ingest_stale_timeout_sec,
            )
            if not consumer.stop():
                return
            new_consumer = StreamConsumer(camera, buf, self._settings)
            new_consumer.bind_async_loop(loop)
            new_consumer.start()
            self._consumers[camera_id] = new_consumer

    def shutdown_all(self) -> None:
        for consumer in list(self._consumers.values()):
            consumer.stop()
        self._consumers.clear()
        self._buffers.clear()
        self._cameras.clear()
