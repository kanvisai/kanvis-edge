"""Orquestación del proceso MediaMTX (RTSP gateway opcional)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import subprocess
from pathlib import Path

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord
from src.discovery.repository import CameraRepository
from src.gateway.config import (
    build_gateway_client_url,
    gateway_config_signature,
    generate_mediamtx_config,
    render_mediamtx_yaml,
)

logger = logging.getLogger(__name__)


class GatewayManager:
    def __init__(self, settings: AppSettings, repository: CameraRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._proc: subprocess.Popen[bytes] | None = None
        self._config_sig: str = ""
        self._last_error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self._settings.rtsp_gateway_enabled

    def _resolve_binary(self) -> str | None:
        custom = self._settings.mediamtx_binary.strip()
        if custom:
            path = Path(custom)
            if path.is_file() and path.stat().st_mode & 0o111:
                return str(path)
            found = shutil.which(custom)
            if found:
                return found
            self._last_error = f"MediaMTX no encontrado: {custom}"
            return None
        found = shutil.which("mediamtx")
        if found:
            return found
        bundled = self._settings.install_root / "bin" / "mediamtx"
        if bundled.is_file():
            return str(bundled)
        self._last_error = "MediaMTX no instalado (apt/install.sh o MEDIAMTX_BINARY)"
        return None

    @property
    def config_path(self) -> Path:
        return self._settings.resolved_mediamtx_config_path

    def _is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def sync_from_repository(self) -> None:
        if not self.is_enabled:
            await self.shutdown()
            return

        async with self._lock:
            cameras = await self._repository.list_cameras()
            sig = gateway_config_signature(cameras, self._settings)
            active = [c for c in cameras if c.output.gateway.enabled]

            if not active:
                await self._shutdown_unlocked()
                self._config_sig = sig
                self._last_error = None
                logger.info("RTSP gateway: sin cámaras con gateway.enabled")
                return

            binary = self._resolve_binary()
            if not binary:
                logger.warning("RTSP gateway deshabilitado en runtime: %s", self._last_error)
                return

            config = generate_mediamtx_config(cameras, self._settings)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(render_mediamtx_yaml(config), encoding="utf-8")

            if sig == self._config_sig and self._is_running():
                return

            await self._shutdown_unlocked()
            try:
                self._proc = subprocess.Popen(
                    [binary, str(self.config_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._config_sig = sig
                self._last_error = None
                logger.info(
                    "RTSP gateway MediaMTX iniciado (pid=%s, puerto=%s, paths=%s)",
                    self._proc.pid,
                    self._settings.rtsp_gateway_port,
                    len(config.get("paths", {})),
                )
            except OSError as exc:
                self._last_error = str(exc)
                logger.exception("No se pudo iniciar MediaMTX")

    async def _shutdown_unlocked(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    async def shutdown(self) -> None:
        async with self._lock:
            await self._shutdown_unlocked()
            self._config_sig = ""

    def get_status(self, cameras: list[CameraRecord] | None = None) -> dict:
        paths: list[dict] = []
        if cameras:
            for cam in cameras:
                if not cam.enabled or not cam.output.gateway.enabled:
                    continue
                paths.append(
                    {
                        "camera_id": cam.camera_id,
                        "path": cam.output.gateway.path or cam.camera_id,
                        "access_mode": cam.output.gateway.access_mode.value,
                        "url_local": build_gateway_client_url(cam, self._settings),
                    }
                )
        return {
            "enabled": self.is_enabled,
            "running": self._is_running(),
            "listen_host": self._settings.rtsp_gateway_listen_host,
            "listen_port": self._settings.rtsp_gateway_port,
            "wan_suggested_port": self._settings.rtsp_gateway_wan_port,
            "mediamtx_binary": self._resolve_binary(),
            "config_path": str(self.config_path),
            "last_error": self._last_error,
            "paths": paths,
        }
