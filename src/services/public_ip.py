"""Detección y caché de la IP pública del edge (sin depender de DDNS/nube)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.config_loader import AppSettings

logger = logging.getLogger(__name__)

# Mismo fichero que WanSyncService para reutilizar la IP ya obtenida
STATE_PATH = Path("/run/kanvis-edge/wan-sync-state.json")


async def fetch_public_ip_from_internet() -> str:
    """Consulta ipify (salida WAN del edge)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://api.ipify.org?format=json")
        resp.raise_for_status()
        ip = str(resp.json().get("ip", "")).strip()
        if not ip:
            raise ValueError("Respuesta ipify sin IP")
        return ip


class PublicIpService:
    """Mantiene la IP pública actualizada en segundo plano."""

    def __init__(
        self,
        settings: AppSettings,
        refresh_interval_sec: int = 300,
    ) -> None:
        self._settings = settings
        self._interval = max(60, refresh_interval_sec)
        self._public_ip = ""
        self._last_sync_at = ""
        self._last_error: str | None = None
        self._running = False
        self._load_state()

    def get_cached(self) -> str:
        return self._public_ip.strip()

    def get_status(self) -> dict:
        return {
            "public_ip": self.get_cached(),
            "last_sync_at": self._last_sync_at,
            "last_error": self._last_error,
            "refresh_interval_seconds": self._interval,
        }

    def _load_state(self) -> None:
        if not STATE_PATH.is_file():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if data.get("public_ip"):
                self._public_ip = str(data["public_ip"]).strip()
            if data.get("last_sync_at"):
                self._last_sync_at = str(data["last_sync_at"])
        except (json.JSONDecodeError, OSError):
            logger.debug("No se pudo leer estado IP pública", exc_info=True)

    def _save_state(self) -> None:
        data: dict = {}
        if STATE_PATH.is_file():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        data["public_ip"] = self._public_ip
        data["last_sync_at"] = self._last_sync_at
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            logger.debug("No se pudo persistir IP pública", exc_info=True)

    async def refresh(self, force: bool = False) -> str:
        try:
            ip = await fetch_public_ip_from_internet()
            if ip != self._public_ip or force:
                logger.info("IP pública del edge: %s", ip)
            self._public_ip = ip
            self._last_sync_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            self._save_state()
            return ip
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("No se pudo obtener IP pública: %s", exc)
            if self._public_ip:
                return self._public_ip
            raise

    async def run_loop(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.refresh()
            except Exception:
                pass
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False
