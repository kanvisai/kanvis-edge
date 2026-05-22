"""WebRtcManager: sesiones por cámara y sincronización con inventario."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, OutputProtocol
from src.ingestion.consumer import StreamConsumerManager
from src.webrtc.publisher import WebRtcMode, WebRtcPublisher, webrtc_mode

if TYPE_CHECKING:
    from src.discovery.repository import CameraRepository

logger = logging.getLogger(__name__)


class WebRtcManager:
    def __init__(
        self,
        settings: AppSettings,
        repository: CameraRepository,
        consumer_manager: StreamConsumerManager,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._consumers = consumer_manager
        self._publishers: dict[str, WebRtcPublisher] = {}
        self._lock = asyncio.Lock()

    def _should_enable(self, camera: CameraRecord) -> bool:
        if not camera.output.webrtc.enabled:
            return False
        if camera.output.protocol == OutputProtocol.RTSP and camera.output.relay.enabled:
            logger.debug(
                "%s: WebRTC con relay RTSP simultáneo — permitido",
                camera.camera_id,
            )
        return True

    def get_publisher(self, camera_id: str) -> WebRtcPublisher | None:
        return self._publishers.get(camera_id)

    async def _get_or_create_publisher(self, camera_id: str) -> WebRtcPublisher:
        existing = self._publishers.get(camera_id)
        if existing:
            return existing
        camera = await self._repository.get(camera_id)
        if not camera:
            raise KeyError(f"Cámara no encontrada: {camera_id}")
        consumer = self._consumers.get_consumer(camera_id)
        buffer = self._consumers.get_buffer(camera_id)
        if not consumer or not buffer:
            raise KeyError(f"Cámara no activa (sin ingesta): {camera_id}")
        video_codec = consumer.metrics.snapshot().get("video_codec")
        pub = WebRtcPublisher(
            camera,
            self._settings,
            consumer.packet_bridge,
            buffer,
            video_codec=video_codec,
        )
        self._publishers[camera_id] = pub
        return pub

    async def sync_from_repository(self) -> None:
        cameras = await self._repository.list_enabled()
        webrtc_ids = {c.camera_id for c in cameras if self._should_enable(c)}

        async with self._lock:
            for cam_id in list(self._publishers):
                if cam_id not in webrtc_ids:
                    await self._publishers[cam_id].close()
                    del self._publishers[cam_id]

            for camera in cameras:
                if not self._should_enable(camera):
                    continue
                cfg = camera.output.webrtc
                if (
                    webrtc_mode(cfg) == WebRtcMode.WHIP
                    and cfg.auto_connect_whip
                    and cfg.signaling_url
                    and camera.camera_id not in self._publishers
                ):
                    try:
                        pub = await self._get_or_create_publisher(camera.camera_id)
                        await pub.start_whip()
                    except Exception as exc:
                        logger.exception(
                            "WHIP auto_connect falló %s: %s",
                            camera.camera_id,
                            exc,
                        )

    async def handle_whep_offer(
        self, camera_id: str, sdp: str, sdp_type: str = "offer"
    ) -> dict[str, str]:
        pub = await self._get_or_create_publisher(camera_id)
        answer = await pub.handle_offer(sdp, sdp_type)
        assert answer is not None
        return {"sdp": answer.sdp, "type": answer.type}

    async def start_whip(self, camera_id: str) -> dict:
        pub = await self._get_or_create_publisher(camera_id)
        state = await pub.start_whip()
        return {
            "camera_id": state.camera_id,
            "mode": state.mode,
            "connection_state": state.connection_state,
            "whip_url": state.whip_url,
        }

    async def rewind(self, camera_id: str, offset_sec: float | None = None) -> dict:
        pub = await self._get_or_create_publisher(camera_id)
        count = await pub.rewind(offset_sec)
        return {"camera_id": camera_id, "packets_queued": count, "offset_sec": offset_sec}

    async def close_session(self, camera_id: str) -> bool:
        pub = self._publishers.pop(camera_id, None)
        if not pub:
            return False
        await pub.close()
        return True

    def list_status(self) -> list[dict]:
        return [
            {
                "camera_id": p.camera_id,
                "mode": p.get_state().mode,
                "connection_state": p.get_state().connection_state,
                "ice_state": p.get_state().ice_state,
                "error": p.get_state().error,
            }
            for p in self._publishers.values()
        ]

    async def shutdown_all(self) -> None:
        for pub in list(self._publishers.values()):
            await pub.close()
        self._publishers.clear()
