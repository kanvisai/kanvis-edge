"""Sincronización WAN: DDNS + reporte de IP pública a la nube Kanvis."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.config_loader import AppSettings, DDNSProvider

logger = logging.getLogger(__name__)

STATE_PATH = Path("/run/kanvis-edge/wan-sync-state.json")


@dataclass
class WanSyncState:
    public_ip: str = ""
    last_sync_at: str = ""
    last_ddns_at: str = ""
    last_ddns_ok: bool = False
    last_ddns_error: str | None = None
    last_cloud_report_at: str = ""
    last_cloud_report_ok: bool = False
    last_cloud_report_error: str | None = None
    last_public_ip_updated_at: str = ""
    ddns_hostname: str = ""
    device_id: str = ""
    device_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WanSyncService:
    """
    Bucle unificado: obtiene IP WAN, actualiza DDNS (opcional)
    y notifica a la API de la nube (opcional).
    """

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._running = False
        self._state = WanSyncState(
            device_id=settings.device_id or "edge",
            device_name=(settings.device_name or "").strip(),
            ddns_hostname=settings.ddns_hostname,
        )
        self._last_reported_ip: str | None = None

    @property
    def state(self) -> WanSyncState:
        return self._state

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _save_state(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps(self._state.to_dict(), indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("No se pudo persistir estado WAN", exc_info=True)

    def _load_state(self) -> None:
        if not STATE_PATH.is_file():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            for key, value in data.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
        except (json.JSONDecodeError, OSError):
            pass

    async def fetch_public_ip(self) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            resp.raise_for_status()
            return str(resp.json()["ip"])

    def _build_ddns_url(self, public_ip: str) -> str:
        provider = self._settings.ddns_provider
        hostname = self._settings.ddns_hostname
        token = (
            self._settings.ddns_token.get_secret_value()
            if self._settings.ddns_token
            else ""
        )
        if provider == DDNSProvider.DUCKDNS:
            if not hostname or not token:
                raise ValueError("DDNS DuckDNS requiere DDNS_HOSTNAME y DDNS_TOKEN")
            return (
                f"https://www.duckdns.org/update"
                f"?domains={hostname}&token={token}&ip={public_ip}"
            )
        if provider == DDNSProvider.NOIP:
            if not hostname:
                raise ValueError("DDNS No-IP requiere DDNS_HOSTNAME")
            return (
                f"https://dynupdate.no-ip.com/nic/update"
                f"?hostname={hostname}&myip={public_ip}"
            )
        if self._settings.ddns_update_url:
            return self._settings.ddns_update_url.replace("{ip}", public_ip)
        raise ValueError("DDNS: proveedor o DDNS_UPDATE_URL no configurados")

    async def update_ddns(self, public_ip: str) -> None:
        url = self._build_ddns_url(public_ip)
        auth = None
        if (
            self._settings.ddns_provider == DDNSProvider.NOIP
            and self._settings.ddns_token
        ):
            auth = (
                self._settings.ddns_hostname,
                self._settings.ddns_token.get_secret_value(),
            )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, auth=auth)
            resp.raise_for_status()
            body = (resp.text or "").strip()[:200]
        self._state.last_ddns_ok = True
        self._state.last_ddns_error = None
        self._state.last_ddns_at = self._now_iso()
        logger.info(
            "DDNS OK (%s): %s -> %s (%s)",
            self._settings.ddns_provider.value,
            self._settings.ddns_hostname,
            public_ip,
            body,
        )

    def _cloud_payload(self, public_ip: str) -> dict[str, str]:
        """
        Body Kanvis backend: POST /api/v1/kanvis-edges/report-public-ip
        (device_name + access_token en JSON; sin Authorization Bearer).
        """
        device_name = (self._settings.device_name or "").strip()
        if not device_name:
            raise ValueError("DEVICE_NAME requerido para reportar IP a Kanvis")
        token = self._settings.cloud_access_token
        if not token or not token.get_secret_value().strip():
            raise ValueError("CLOUD_ACCESS_TOKEN (o CLOUD_REPORT_TOKEN) requerido")
        payload: dict[str, str] = {
            "device_name": device_name,
            "access_token": token.get_secret_value(),
            "public_ip": public_ip.strip(),
        }
        # Etiqueta de host en backend (no implica DDNS_ENABLED ni resolución DNS pública).
        host_label = (self._settings.ddns_hostname or "").strip()
        if host_label:
            payload["ddns_hostname"] = host_label
        return payload

    async def report_to_cloud(self, public_ip: str) -> None:
        url = self._settings.cloud_report_url.strip()
        if not url:
            raise ValueError("CLOUD_REPORT_URL no configurada")
        headers = {"Content-Type": "application/json", "User-Agent": "kanvis-edge/1.0"}
        payload = self._cloud_payload(public_ip)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 401:
                raise ValueError(
                    "Invalid device_name or access_token (HTTP 401); revisa DEVICE_NAME y CLOUD_ACCESS_TOKEN"
                )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        if isinstance(data, dict):
            self._state.last_public_ip_updated_at = str(
                data.get("last_public_ip_updated_at", "")
            )
            if data.get("device_name"):
                self._state.device_name = str(data["device_name"]).strip()
            if data.get("public_ip"):
                self._state.public_ip = str(data["public_ip"])
        self._state.last_cloud_report_ok = True
        self._state.last_cloud_report_error = None
        self._state.last_cloud_report_at = self._now_iso()
        self._last_reported_ip = public_ip
        logger.info(
            "Reporte IP nube OK: device_name=%s public_ip=%s",
            payload["device_name"],
            public_ip,
        )

    async def sync_once(self, force_cloud: bool = False) -> WanSyncState:
        """Una pasada: IP + DDNS + nube (si aplica)."""
        self._load_state()
        public_ip = await self.fetch_public_ip()
        self._state.public_ip = public_ip
        self._state.last_sync_at = self._now_iso()

        if self._settings.ddns_enabled:
            try:
                await self.update_ddns(public_ip)
            except Exception as exc:
                self._state.last_ddns_ok = False
                self._state.last_ddns_error = str(exc)
                logger.exception("DDNS falló")

        if self._settings.cloud_report_enabled:
            ip_changed = public_ip != self._last_reported_ip
            always = not self._settings.cloud_report_on_ip_change_only
            if force_cloud or always or ip_changed or not self._state.last_cloud_report_ok:
                try:
                    await self.report_to_cloud(public_ip)
                except ValueError as exc:
                    self._state.last_cloud_report_ok = False
                    self._state.last_cloud_report_error = str(exc)
                    # 401 = credenciales; no hace falta traceback completo en cada arranque
                    logger.warning("Reporte nube: %s", exc)
                except Exception as exc:
                    self._state.last_cloud_report_ok = False
                    self._state.last_cloud_report_error = str(exc)
                    logger.exception("Reporte nube falló")
            else:
                logger.debug("Omitiendo reporte nube: IP sin cambios (%s)", public_ip)

        self._save_state()
        return self._state

    async def run_loop(self) -> None:
        self._running = True
        interval = self._settings.effective_wan_sync_interval
        while self._running:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("Error en ciclo WAN sync")
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False


# Alias retrocompatible
DDNSSyncService = WanSyncService
