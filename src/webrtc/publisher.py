"""WebRTC publisher: sesiones WHEP (answer) y WHIP (push)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

import httpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

try:
    from aiortc import RTCBundlePolicy
except ImportError:  # aiortc < 1.11
    RTCBundlePolicy = None  # type: ignore[misc, assignment]
from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, CameraWebRTCOutput
from src.ingestion.buffer import PacketCircularBuffer
from src.ingestion.bridge import PacketBridge
from src.webrtc.track import H264PacketVideoTrack

logger = logging.getLogger(__name__)

# Marcador para comprobar en /api/v1/health → features.webrtc_rtc_configuration
WEBRTC_PEER_CONFIG_VERSION = "RTCConfiguration-2025-05"


class WebRtcMode(str, Enum):
    WHEP = "whep"
    WHIP = "whip"


@dataclass
class WebRtcSessionState:
    camera_id: str
    mode: str
    connection_state: str
    ice_state: str
    rewind_packets_pending: int = 0
    whip_url: str = ""
    error: str | None = None


def webrtc_mode(cfg: CameraWebRTCOutput) -> WebRtcMode:
    try:
        return WebRtcMode(str(cfg.mode).lower())
    except ValueError:
        return WebRtcMode.WHEP


def _rtc_configuration(camera: CameraRecord) -> RTCConfiguration:
    """aiortc >=1.11 exige RTCConfiguration (no dict) con bundlePolicy."""
    urls = [u for u in camera.output.webrtc.stun_urls if u.strip()]
    if not urls:
        urls = ["stun:stun.l.google.com:19302"]
    ice_url: str | list[str] = urls[0] if len(urls) == 1 else urls
    ice_servers = [RTCIceServer(urls=ice_url)]
    if RTCBundlePolicy is not None:
        return RTCConfiguration(
            iceServers=ice_servers,
            bundlePolicy=RTCBundlePolicy.MAX_BUNDLE,
        )
    return RTCConfiguration(iceServers=ice_servers)


def _new_peer_connection(camera: CameraRecord) -> RTCPeerConnection:
    configuration = _rtc_configuration(camera)
    if isinstance(configuration, dict):
        raise TypeError(
            "RTCPeerConnection requiere RTCConfiguration, no dict "
            f"(código desplegado: {WEBRTC_PEER_CONFIG_VERSION})"
        )
    return RTCPeerConnection(configuration=configuration)


class WebRtcPublisher:
    """Una sesión WebRTC por cámara."""

    def __init__(
        self,
        camera: CameraRecord,
        settings: AppSettings,
        packet_bridge: PacketBridge,
        buffer: PacketCircularBuffer,
        video_codec: str | None = None,
    ) -> None:
        self._camera = camera
        self._settings = settings
        self._packet_bridge = packet_bridge
        self._buffer = buffer
        self._video_codec = video_codec
        self._pc: RTCPeerConnection | None = None
        self._track: H264PacketVideoTrack | None = None
        self._live_queue: asyncio.Queue | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None
        self._whip_task: asyncio.Task | None = None

    @property
    def camera_id(self) -> str:
        return self._camera.camera_id

    def get_state(self) -> WebRtcSessionState:
        pc = self._pc
        return WebRtcSessionState(
            camera_id=self.camera_id,
            mode=webrtc_mode(self._camera.output.webrtc).value,
            connection_state=pc.connectionState if pc else "new",
            ice_state=pc.iceConnectionState if pc else "new",
            rewind_packets_pending=self._track.rewind_pending if self._track else 0,
            whip_url=self._camera.output.webrtc.signaling_url,
            error=self._last_error,
        )

    def _ensure_track(self) -> H264PacketVideoTrack:
        if self._track is None:
            self._live_queue = self._packet_bridge.subscribe()
            self._track = H264PacketVideoTrack(
                self._live_queue,
                self._buffer,
                target_fps=self._camera.source.fps,
                video_codec=self._video_codec,
            )
        return self._track

    async def handle_offer(
        self, sdp: str, sdp_type: str = "offer"
    ) -> RTCSessionDescription:
        """WHEP-like: cliente envía offer, devolvemos answer con vídeo del búfer."""
        async with self._lock:
            await self.close()
            self._pc = _new_peer_connection(self._camera)
            track = self._ensure_track()
            primed = await track.prime_from_buffer(
                self._camera.output.webrtc.rewind_offset_sec
            )
            if primed == 0:
                logger.warning(
                    "WebRTC %s: búfer vacío al conectar; espera ingesta RTSP",
                    self.camera_id,
                )
            self._pc.addTrack(track)
            await self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp, type=sdp_type)
            )
            answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(answer)
            await self._wait_ice_gathering()
            return self._pc.localDescription

    async def start_whip(self) -> WebRtcSessionState:
        """WHIP: crea offer y lo publica en signaling_url."""
        cfg = self._camera.output.webrtc
        if not cfg.signaling_url:
            raise ValueError("webrtc.signaling_url requerido para modo whip")

        async with self._lock:
            await self.close()
            self._pc = _new_peer_connection(self._camera)
            track = self._ensure_track()
            self._pc.addTrack(track)
            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)
            await self._wait_ice_gathering()
            local = self._pc.localDescription
            assert local is not None
            await self._post_whip_sdp(cfg.signaling_url, local.sdp, local.type)
            logger.info("WHIP conectado para %s", self.camera_id)
            return self.get_state()

    async def _post_whip_sdp(self, url: str, sdp: str, sdp_type: str) -> None:
        headers = {
            "Content-Type": "application/sdp",
            "Accept": "application/sdp",
        }
        token = self._settings.cloud_access_token
        if token:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, content=sdp, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"WHIP HTTP {resp.status_code}: {resp.text[:300]}")
            answer_sdp = resp.text
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

    async def rewind(self, offset_sec: float | None = None) -> int:
        track = self._ensure_track()
        offset = offset_sec or self._camera.output.webrtc.rewind_offset_sec
        return await track.rewind(offset)

    async def _wait_ice_gathering(self, timeout: float = 8.0) -> None:
        pc = self._pc
        if pc is None or pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _on_ice() -> None:
            if pc.iceGatheringState == "complete":
                done.set()

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("ICE gathering timeout %s", self.camera_id)

    async def close(self) -> None:
        if self._whip_task and not self._whip_task.done():
            self._whip_task.cancel()
        if self._track:
            self._track.stop()
            self._track = None
        if self._live_queue:
            self._packet_bridge.unsubscribe(self._live_queue)
            self._live_queue = None
        if self._pc:
            await self._pc.close()
            self._pc = None
