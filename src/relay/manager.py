"""RelayManager: orquestación de relays RTSP por cámara con hot-reload."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, OutputProtocol
from src.relay.worker import FfmpegRtspRelay

if TYPE_CHECKING:
    from src.discovery.repository import CameraRepository
    from src.schedule.service import OperatingScheduleService

logger = logging.getLogger(__name__)


class RelayManager:
    def __init__(
        self,
        settings: AppSettings,
        repository: CameraRepository,
        schedule: OperatingScheduleService | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._schedule = schedule
        self._relays: dict[str, FfmpegRtspRelay] = {}
        self._ports: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def get_relay(self, camera_id: str) -> FfmpegRtspRelay | None:
        return self._relays.get(camera_id)

    def get_listen_port(self, camera_id: str) -> int | None:
        return self._ports.get(camera_id)

    def _should_run_relay(self, camera: CameraRecord) -> bool:
        if not camera.output.relay.enabled:
            return False
        if camera.output.protocol == OutputProtocol.WEBRTC:
            return False
        return True

    def _allocate_port(self, camera: CameraRecord, index: int) -> int:
        if camera.output.relay.listen_port:
            return camera.output.relay.listen_port
        return self._settings.edge_rtsp_port + index

    async def sync_from_repository(self) -> None:
        if self._schedule is not None and not self._schedule.is_operating_now():
            async with self._lock:
                if self._relays:
                    logger.info(
                        "Horario operativo inactivo — deteniendo rebroadcast RTSP"
                    )
                    for relay in self._relays.values():
                        relay.stop()
                    self._relays.clear()
                    self._ports.clear()
            return

        cameras = await self._repository.list_all()
        relay_cameras = [c for c in cameras if self._should_run_relay(c) and c.enabled]
        active_ids = {c.camera_id for c in relay_cameras}

        async with self._lock:
            for cam_id in list(self._relays):
                if cam_id not in active_ids:
                    self._relays[cam_id].stop()
                    del self._relays[cam_id]
                    del self._ports[cam_id]

            used_ports: set[int] = set(self._ports.values())
            for index, camera in enumerate(relay_cameras):
                port = self._allocate_port(camera, index)
                if port in used_ports and self._ports.get(camera.camera_id) != port:
                    logger.error(
                        "Puerto RTSP %d en conflicto para %s",
                        port,
                        camera.camera_id,
                    )
                    continue
                used_ports.add(port)
                self._ports[camera.camera_id] = port

                existing = self._relays.get(camera.camera_id)
                if existing is None:
                    relay = FfmpegRtspRelay(camera, self._settings, port)
                    relay.start()
                    self._relays[camera.camera_id] = relay
                    continue

                changed = existing.update_camera(camera, port)
                if changed:
                    logger.info(
                        "Config relay cambió para %s — reiniciando",
                        camera.camera_id,
                    )
                    existing.stop()
                    new_relay = FfmpegRtspRelay(camera, self._settings, port)
                    new_relay.start()
                    self._relays[camera.camera_id] = new_relay
                elif not existing.is_running:
                    existing.start()

    async def run_inventory_watcher(self) -> None:
        while True:
            try:
                await self.sync_from_repository()
            except Exception:
                logger.exception("Error sincronizando relays")
            await asyncio.sleep(30)

    def start_camera(self, camera_id: str) -> bool:
        if self._schedule is not None and not self._schedule.is_operating_now():
            return False
        relay = self._relays.get(camera_id)
        if relay is None:
            return False
        relay.start()
        return True

    def stop_camera(self, camera_id: str) -> bool:
        relay = self._relays.get(camera_id)
        if relay is None:
            return False
        relay.stop()
        return True

    def shutdown_all(self) -> None:
        for relay in self._relays.values():
            relay.stop()
        self._relays.clear()
        self._ports.clear()

    def list_status(self) -> list[dict]:
        return [r.get_status() for r in self._relays.values()]
