"""NetworkScanner: descubrimiento RTSP y ONVIF en LAN."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass

from pydantic import SecretStr

from src.config_loader import AppSettings
from src.discovery.models import CameraBufferSettings, CameraRecord, CameraSource
from src.discovery.repository import CameraRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDevice:
    ip_address: str
    rtsp_port: int
    source: str  # "port_scan" | "onvif"


class NetworkScanner:
    """Escaneo asíncrono de segmentos LAN y autoregistro opcional."""

    def __init__(
        self,
        settings: AppSettings,
        repository: CameraRepository,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._running = False

    def _iter_hosts(self) -> list[str]:
        network = ipaddress.ip_network(self._settings.discovery_subnet, strict=False)
        return [str(host) for host in network.hosts()]

    async def _check_rtsp_port(self, host: str, port: int, timeout: float) -> bool:
        try:
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def scan_rtsp_ports(self) -> list[DiscoveredDevice]:
        """Escaneo concurrente de puertos RTSP en la subred."""
        hosts = self._iter_hosts()
        ports = self._settings.rtsp_scan_ports
        sem = asyncio.Semaphore(64)
        found: list[DiscoveredDevice] = []

        async def probe(host: str, port: int) -> None:
            async with sem:
                if await self._check_rtsp_port(host, port, timeout=1.0):
                    found.append(
                        DiscoveredDevice(
                            ip_address=host,
                            rtsp_port=port,
                            source="port_scan",
                        )
                    )

        tasks = [probe(h, p) for h in hosts for p in ports]
        await asyncio.gather(*tasks, return_exceptions=True)
        return found

    async def scan_onvif(self, timeout: int = 5) -> list[DiscoveredDevice]:
        """WS-Discovery ONVIF (bloqueante en thread pool)."""

        def _discover() -> list[DiscoveredDevice]:
            results: list[DiscoveredDevice] = []
            try:
                from wsdiscovery import WSDiscovery
                from wsdiscovery.scope import Scope

                wsd = WSDiscovery()
                wsd.start()
                services = wsd.searchServices(
                    scopes=[Scope("onvif://www.onvif.org/Profile")],
                    timeout=timeout,
                )
                for svc in services:
                    xaddrs = svc.getXAddrs()
                    if not xaddrs:
                        continue
                    # Extraer host del primer XAddr
                    addr = xaddrs[0]
                    if "://" in addr:
                        addr = addr.split("://", 1)[1]
                    host = addr.split("/")[0].split(":")[0]
                    results.append(
                        DiscoveredDevice(
                            ip_address=host,
                            rtsp_port=554,
                            source="onvif",
                        )
                    )
                wsd.stop()
            except ImportError:
                logger.warning("WSDiscovery no disponible; omitiendo ONVIF")
            except Exception:
                logger.exception("Error en descubrimiento ONVIF")
            return results

        return await asyncio.to_thread(_discover)

    async def run_discovery_pass(self) -> list[DiscoveredDevice]:
        """Una pasada completa: RTSP + ONVIF, deduplicada por IP."""
        rtsp = await self.scan_rtsp_ports()
        onvif = await self.scan_onvif()
        seen: set[tuple[str, int]] = set()
        merged: list[DiscoveredDevice] = []
        for dev in rtsp + onvif:
            key = (dev.ip_address, dev.rtsp_port)
            if key not in seen:
                seen.add(key)
                merged.append(dev)
        logger.info("Descubiertos %d dispositivos", len(merged))
        return merged

    async def provision_discovered(
        self,
        devices: list[DiscoveredDevice],
        default_user: str = "",
        default_password: str = "",
    ) -> int:
        """Registra cámaras nuevas en el inventario sin duplicar IP:puerto."""
        existing = await self._repository.list_all()
        known = {(c.source.host, c.source.port) for c in existing}
        added = 0
        for dev in devices:
            if (dev.ip_address, dev.rtsp_port) in known:
                continue
            camera_id = f"auto-{dev.ip_address.replace('.', '-')}-{dev.rtsp_port}"
            record = CameraRecord(
                camera_id=camera_id,
                enabled=False,
                source=CameraSource(
                    host=dev.ip_address,
                    port=dev.rtsp_port,
                    username=default_user,
                    password=SecretStr(default_password),
                ),
                buffer=CameraBufferSettings(),
            )
            try:
                await self._repository.create(record)
                added += 1
                known.add((dev.ip_address, dev.rtsp_port))
            except ValueError:
                pass
        return added

    async def run_loop(self) -> None:
        """Bucle periódico de descubrimiento."""
        self._running = True
        while self._running:
            try:
                devices = await self.run_discovery_pass()
                await self.provision_discovered(devices)
            except Exception:
                logger.exception("Error en ciclo de descubrimiento")
            await asyncio.sleep(self._settings.discovery_interval_seconds)

    def stop(self) -> None:
        self._running = False
