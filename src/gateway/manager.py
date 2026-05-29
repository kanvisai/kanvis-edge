"""Orquestacion del proceso MediaMTX (RTSP gateway opcional)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import subprocess
import time
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
        self._mediamtx_started_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self._settings.rtsp_gateway_enabled

    def _resolve_binary(self) -> str | None:
        """Ruta al binario MediaMTX (PATH, INSTALL_ROOT/bin o ruta absoluta)."""
        custom = self._settings.mediamtx_binary.strip()
        candidates: list[Path] = []
        if custom:
            candidates.append(Path(custom))
            if "/" not in custom:
                candidates.append(self._settings.install_root / "bin" / custom)
        candidates.append(self._settings.install_root / "bin" / "mediamtx")

        for path in candidates:
            try:
                resolved = path.expanduser().resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved.stat().st_mode & 0o111:
                return str(resolved)

        for name in (custom, "mediamtx"):
            if not name:
                continue
            found = shutil.which(name)
            if found:
                return found

        label = custom or "mediamtx"
        bundled = self._settings.install_root / "bin" / "mediamtx"
        self._last_error = (
            f"MediaMTX no encontrado: {label!r} (probado PATH y {bundled})"
        )
        return None

    @property
    def config_path(self) -> Path:
        return self._settings.resolved_mediamtx_config_path

    def _is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def mediamtx_running(self) -> bool:
        """True si el subproceso MediaMTX sigue vivo."""
        return self._is_running()

    def _record_mediamtx_exit(self) -> None:
        """Si MediaMTX termino, guarda stderr y limpia el handle."""
        if self._proc is None:
            return
        code = self._proc.poll()
        if code is None:
            return
        err_tail = b""
        if self._proc.stderr:
            try:
                err_tail = self._proc.stderr.read()[-800:]
            except OSError:
                pass
        self._last_error = (
            f"MediaMTX termino (exit={code}): "
            f"{err_tail.decode('utf-8', errors='replace').strip() or 'sin stderr'}"
        )
        logger.error("RTSP gateway: %s", self._last_error)
        self._proc = None

    async def ensure_mediamtx_running(self) -> None:
        """
        Comprueba MediaMTX y lo vuelve a levantar si murio.

        Llamado por el watchdog periodico (mas rapido que el inventario cada 30s).
        """
        if not self.is_enabled:
            return
        self._record_mediamtx_exit()
        if self._is_running():
            return
        logger.warning("MediaMTX no esta en ejecucion; reintentando arranque")
        await self.sync_from_repository()

    async def restart_mediamtx_scheduled(self) -> None:
        """Reinicio preventivo opcional (MEDIAMTX_RESTART_INTERVAL_SEC > 0)."""
        interval = self._settings.mediamtx_restart_interval_sec
        if interval <= 0 or not self.is_enabled:
            return
        self._record_mediamtx_exit()
        if not self._is_running():
            return
        if time.monotonic() - self._mediamtx_started_at < interval:
            return
        logger.info(
            "Reinicio programado de MediaMTX tras %.1f h (MEDIAMTX_RESTART_INTERVAL_SEC)",
            interval / 3600.0,
        )
        async with self._lock:
            await self._shutdown_unlocked()
            self._config_sig = ""
        await self.sync_from_repository()

    async def sync_from_repository(self) -> None:
        if not self.is_enabled:
            await self.shutdown()
            return

        async with self._lock:
            cameras = await self._repository.list_all()
            sig = gateway_config_signature(cameras, self._settings)
            active = [c for c in cameras if c.output.gateway.enabled]

            if not active:
                await self._shutdown_unlocked()
                self._config_sig = sig
                self._last_error = None
                logger.info("RTSP gateway: sin camaras con gateway.enabled")
                return

            binary = self._resolve_binary()
            if not binary:
                logger.warning("RTSP gateway deshabilitado en runtime: %s", self._last_error)
                return

            config = generate_mediamtx_config(cameras, self._settings)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(render_mediamtx_yaml(config), encoding="utf-8")

            self._record_mediamtx_exit()
            if sig == self._config_sig and self._is_running():
                return

            await self._shutdown_unlocked()
            try:
                self._proc = subprocess.Popen(
                    [binary, str(self.config_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                await asyncio.sleep(0.4)
                if self._proc.poll() is not None:
                    err_tail = b""
                    if self._proc.stderr:
                        err_tail = self._proc.stderr.read()[-800:]
                    self._last_error = (
                        f"MediaMTX termino al arrancar (exit={self._proc.returncode}): "
                        f"{err_tail.decode('utf-8', errors='replace').strip() or 'sin stderr'}"
                    )
                    self._proc = None
                    logger.error("RTSP gateway: %s", self._last_error)
                    return
                self._config_sig = sig
                self._last_error = None
                self._mediamtx_started_at = time.monotonic()
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
