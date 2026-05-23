"""Rutas REST del Gateway."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, SecretStr

from src.api.dispatcher import VideoDispatcher
from src.config_loader import AppSettings, get_settings
from src.discovery.models import (
    CameraBufferSettings,
    CameraCreatePayload,
    CameraOutput,
    CameraRecord,
    CameraRelayOutput,
    CameraSource,
    CameraWebRTCOutput,
)
from src.testing.rtsp_probe import probe_rtsp_stream
from src.testing.snapshot import SnapshotError, capture_jpeg_from_rtsp, local_listen_url
from src.discovery.rtsp_urls import default_gateway_path
from src.discovery.repository import CameraRepository
from src.ingestion.consumer import StreamConsumerManager
from src.discovery.scanner import NetworkScanner
from src.gateway.config import build_gateway_access_urls, build_gateway_client_url
from src.gateway.manager import GatewayManager
from src.relay.manager import RelayManager
from src.webrtc.manager import WebRtcManager
from src.relay.worker import _mask_url, build_relay_urls
from src.discovery.models import ExternalAccessMode

logger = logging.getLogger(__name__)
from src.schedule.models import OperatingSchedule as OperatingSchedulePayload
from src.schedule.service import OperatingScheduleService, require_operating_now
from src.services.wan_sync import WanSyncService
from src.brands import list_brand_slugs, load_brand_profile
from src.brands.registry import default_brands_dir

router = APIRouter(prefix="/api/v1")


def get_repository(request: Request) -> CameraRepository:
    return request.app.state.camera_repository


def get_dispatcher(request: Request) -> VideoDispatcher:
    return request.app.state.video_dispatcher


def get_consumer_manager(request: Request) -> StreamConsumerManager:
    return request.app.state.consumer_manager


def get_relay_manager(request: Request) -> RelayManager:
    return request.app.state.relay_manager


def get_gateway_manager(request: Request) -> GatewayManager:
    return request.app.state.gateway_manager


def get_webrtc_manager(request: Request) -> WebRtcManager:
    return request.app.state.webrtc_manager


def get_app_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_wan_sync(request: Request) -> WanSyncService | None:
    return getattr(request.app.state, "wan_sync_service", None)


def get_schedule_service(request: Request) -> OperatingScheduleService:
    return request.app.state.operating_schedule_service


@router.get("/health")
async def health(request: Request) -> dict:
    from src.webrtc.publisher import WEBRTC_PEER_CONFIG_VERSION

    public_ip = ""
    pip = getattr(request.app.state, "public_ip_service", None)
    if pip:
        public_ip = pip.get_cached()

    return {
        "status": "ok",
        "ui_version": "2026-05-rtsp-gateway-v2",
        "edge_build": "rtsp-gateway-playback-v2",
        "public_ip": public_ip,
        "features": {
            "rtsp_probe_tools": True,
            "rtsp_probe_paths": [
                "/api/v1/tools/rtsp-probe",
                "/api/v1/tools/rtsp-probe-meta",
                "/api/v1/rtsp/probe",
            ],
            "rtsp_probe_codec": True,
            "rtsp_probe_json": True,
            "webrtc_peer_config": WEBRTC_PEER_CONFIG_VERSION,
        },
    }


@router.get("/operating-schedule")
async def read_operating_schedule(
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
) -> dict:
    """Horario de búfer/ingesta y broadcast (persistido en config/operating_schedule.json)."""
    sched = schedule_svc.get()
    status = schedule_svc.get_status()
    return {
        "schedule": sched.model_dump(mode="json"),
        "status": status,
    }


@router.put("/operating-schedule")
async def put_operating_schedule(
    body: Annotated[OperatingSchedulePayload, Body()],
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
    consumer_manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> dict:
    """Actualiza el horario y aplica de inmediato (sin reiniciar el servicio)."""
    updated = schedule_svc.update(body)
    loop = __import__("asyncio").get_running_loop()
    await consumer_manager.sync_from_repository(loop)
    await relay_manager.sync_from_repository()
    return {
        "schedule": updated.model_dump(mode="json"),
        "status": schedule_svc.get_status(),
    }


@router.get("/config")
async def get_public_config(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Resumen de configuración no sensible (para UI futura)."""
    return {
        "device_id": settings.device_id or None,
        "device_name": settings.device_name or None,
        "buffer_duration_seconds": settings.buffer_duration_seconds,
        "default_playback_offset_sec": settings.default_playback_offset_sec,
        "default_playback_test_offset_sec": settings.default_playback_test_offset_sec,
        "edge_rtsp_port": settings.edge_rtsp_port,
        "rtsp_gateway_enabled": settings.rtsp_gateway_enabled,
        "rtsp_gateway_port": settings.rtsp_gateway_port,
        "rtsp_gateway_wan_port": settings.rtsp_gateway_wan_port,
        "discovery_enabled": settings.discovery_enabled,
    }


@router.get("/system/info")
async def system_info(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Información de despliegue en hardware (Fase 5)."""
    device = settings.device_id or "edge"
    distro = "unknown"
    distro_path = Path("/run/kanvis-edge/distro")
    if distro_path.is_file():
        distro = distro_path.read_text(encoding="utf-8").strip()
    deploy_method = "native_systemd"
    if Path("/.dockerenv").exists():
        deploy_method = "docker"
    return {
        "device_id": device,
        "device_name": settings.device_name or None,
        "deploy_method": deploy_method,
        "detected_distro": distro,
        "network_mode": settings.network_mode,
        "ap_ssid_prefix": settings.ap_ssid_prefix,
        "ap_ssid_hint": f"{settings.ap_ssid_prefix}-{device}",
        "ap_ip": settings.ap_ip,
        "webui_url": f"http://{settings.ap_ip}:{settings.edge_api_port}/",
        "wlan_interface": settings.wlan_interface,
        "lan_interface": settings.lan_interface,
        "edge_api_port": settings.edge_api_port,
        "install_root": str(settings.install_root),
        "ddns_enabled": settings.ddns_enabled,
        "cloud_report_enabled": settings.cloud_report_enabled,
        "ddns_hostname": settings.ddns_hostname,
    }


@router.get("/connectivity/public-ip")
async def connectivity_public_ip(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    refresh: bool = Query(default=False, description="Forzar consulta ipify ahora"),
) -> dict:
    """IP pública actual del edge (automática; no hace falta EDGE_PANEL_PUBLIC_URL)."""
    svc = getattr(request.app.state, "public_ip_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Servicio de IP pública no disponible")
    try:
        if refresh:
            ip = await svc.refresh(force=True)
        else:
            ip = svc.get_cached() or await svc.refresh()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No se pudo detectar IP pública: {exc}",
        ) from exc
    urls = _resolve_panel_urls(request, settings)
    return {
        **svc.get_status(),
        "panel_url": urls["api_base"],
        "url_note": urls["url_note"],
    }


@router.post("/connectivity/public-ip/refresh")
async def connectivity_public_ip_refresh(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Actualiza la IP pública ahora (p. ej. tras cambio de ISP)."""
    return await connectivity_public_ip(request, settings, refresh=True)


@router.get("/connectivity/status")
async def connectivity_status(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    wan: Annotated[WanSyncService | None, Depends(get_wan_sync)],
    request: Request,
) -> dict:
    """Estado DDNS y último reporte a la nube."""
    port_matrix_url = "/docs/PORT_FORWARDING.md"
    base = {
        "wan_sync_enabled": settings.wan_sync_enabled,
        "ddns_enabled": settings.ddns_enabled,
        "cloud_report_enabled": settings.cloud_report_enabled,
        "interval_seconds": settings.effective_wan_sync_interval,
        "device_id": settings.device_id or "edge",
        "device_name": settings.device_name or None,
        "port_forwarding_doc": port_matrix_url,
    }
    if settings.ddns_hostname and settings.ddns_provider.value == "duckdns":
        base["ddns_fqdn_hint"] = f"{settings.ddns_hostname}.duckdns.org"
    elif settings.ddns_hostname:
        base["ddns_fqdn_hint"] = settings.ddns_hostname

    pip = getattr(request.app.state, "public_ip_service", None)
    if pip:
        base["public_ip_auto"] = pip.get_status()
    if wan is None:
        base["state"] = None
        if not pip or not pip.get_cached():
            base["message"] = (
                "WAN sync opcional inactivo; la IP pública se detecta igual en segundo plano"
            )
        return base
    return {**base, "state": wan.state.to_dict()}


@router.post("/connectivity/sync")
async def connectivity_sync_now(
    wan: Annotated[WanSyncService | None, Depends(get_wan_sync)],
    force: bool = Query(default=False, description="Forzar reporte nube aunque IP no cambie"),
) -> dict:
    """Fuerza una sincronización inmediata de IP (DDNS + nube)."""
    if wan is None:
        raise HTTPException(
            status_code=503,
            detail="WAN sync no configurado",
        )
    state = await wan.sync_once(force_cloud=force)
    return {"ok": True, "state": state.to_dict()}


@router.get("/brands")
async def list_brands(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Marcas disponibles (ficheros en config/brands/*.json)."""
    root = default_brands_dir(settings.config_dir)
    slugs = list_brand_slugs(root)
    items = []
    for slug in slugs:
        try:
            profile = load_brand_profile(slug, root)
            items.append(
                {
                    "slug": slug,
                    "brand": profile.brand,
                    "version": profile.version,
                    "models": profile.models,
                    "rtsp": {
                        "stream_template": profile.protocols.rtsp.stream_template,
                        "playback_template": profile.protocols.rtsp.playback_template,
                        "time_format": profile.protocols.rtsp.time_format,
                        "requires_utc": profile.protocols.rtsp.requires_utc,
                    },
                    "default_channels": [
                        {"id": ch.id, "label": ch.label}
                        for ch in profile.default_channels
                    ]
                    or [{"id": "101", "label": "Principal"}, {"id": "102", "label": "Sub"}],
                }
            )
        except (FileNotFoundError, ValueError):
            continue
    return {"brands_dir": str(root), "brands": items}


class CameraProbeRequest(BaseModel):
    """Prueba RTSP sin guardar cámara (vista previa JPEG)."""

    host: str
    port: int = Field(default=554, ge=1, le=65535)
    username: str = ""
    password: str = ""
    brand: str = ""
    channel: str = "101"
    transport: str = "tcp"


def _probe_record_from_body(body: CameraProbeRequest) -> CameraRecord:
    host = body.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="host requerido")
    return CameraRecord(
        camera_id="probe",
        label="probe",
        enabled=True,
        source=CameraSource(
            host=host,
            port=body.port,
            username=body.username,
            password=SecretStr(body.password),
            brand=body.brand.strip(),
            channel=body.channel.strip() or "101",
            transport=body.transport or "tcp",
        ),
        output=CameraOutput(),
        buffer=CameraBufferSettings(),
    )


async def _probe_rtsp_codec_meta(
    body: CameraProbeRequest,
    settings: AppSettings,
) -> dict:
    record = _probe_record_from_body(body)
    try:
        url = record.rtsp_url(settings=settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transport = record.source.transport or "tcp"
    tout = settings.snapshot_timeout_sec
    try:
        probe_info = await probe_rtsp_stream(
            url,
            ffmpeg_path=settings.ffmpeg_path,
            transport=transport,
            timeout_sec=tout,
        )
    except SnapshotError as exc:
        return {
            "ok": False,
            "codec_detected": False,
            "error": str(exc),
            "rtsp_url_masked": _mask_url(url),
        }
    except Exception as exc:
        logger.exception("probe-meta inesperado")
        return {
            "ok": False,
            "codec_detected": False,
            "error": str(exc),
            "rtsp_url_masked": _mask_url(url),
        }
    out = probe_info.as_dict()
    out["ok"] = True
    out["codec_detected"] = bool(probe_info.codec_name)
    out["rtsp_url_masked"] = _mask_url(url)
    return out


async def _probe_rtsp_capture_jpeg(
    body: CameraProbeRequest,
    settings: AppSettings,
) -> tuple[bytes, str]:
    record = _probe_record_from_body(body)
    try:
        url = record.rtsp_url(settings=settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transport = record.source.transport or "tcp"
    tout = settings.snapshot_timeout_sec
    jpeg = await capture_jpeg_from_rtsp(
        url,
        ffmpeg_path=settings.ffmpeg_path,
        transport=transport,
        timeout_sec=tout,
    )
    return jpeg, url


async def _probe_camera_rtsp_impl(
    body: CameraProbeRequest,
    settings: AppSettings,
) -> Response:
    """Captura un frame JPEG desde RTSP (sin cabeceras de códec; evita HTTP 500)."""
    try:
        jpeg, url = await _probe_rtsp_capture_jpeg(body, settings)
    except HTTPException:
        raise
    except SnapshotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("rtsp-probe falló")
        raise HTTPException(status_code=502, detail=f"Probe RTSP: {exc}") from exc
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def _probe_camera_rtsp_json_impl(
    body: CameraProbeRequest,
    settings: AppSettings,
) -> dict:
    """JPEG + códec en un solo JSON (recomendado para la UI)."""
    record = _probe_record_from_body(body)
    try:
        url = record.rtsp_url(settings=settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transport = (body.transport or "tcp").strip()
    tout = settings.snapshot_timeout_sec
    codec_error = ""
    probe_info = None
    try:
        probe_info = await probe_rtsp_stream(
            url,
            ffmpeg_path=settings.ffmpeg_path,
            transport=transport,
            timeout_sec=min(tout, 10.0),
        )
    except SnapshotError as exc:
        codec_error = str(exc)
        logger.info("Códec no detectado en probe: %s", exc)
    try:
        jpeg = await capture_jpeg_from_rtsp(
            url,
            ffmpeg_path=settings.ffmpeg_path,
            transport=transport,
            timeout_sec=tout,
        )
    except HTTPException:
        raise
    except SnapshotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("rtsp-probe-json falló")
        raise HTTPException(status_code=502, detail=f"Probe RTSP: {exc}") from exc

    out: dict = {
        "ok": True,
        "image_base64": base64.b64encode(jpeg).decode("ascii"),
        "content_type": "image/jpeg",
        "rtsp_url_masked": _mask_url(url),
        "codec_detected": bool(probe_info and probe_info.codec_name),
        "codec_error": codec_error,
    }
    if probe_info:
        out.update(probe_info.as_dict())
    return out


@router.post("/tools/rtsp-probe-json")
async def probe_camera_rtsp_json(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Vista previa + códec en JSON (un solo POST, sin cabeceras HTTP raras)."""
    return await _probe_camera_rtsp_json_impl(body, settings)


@router.post("/rtsp/probe-json")
async def probe_camera_rtsp_json_alias(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    return await _probe_camera_rtsp_json_impl(body, settings)


@router.post("/tools/rtsp-probe-meta")
async def probe_camera_rtsp_meta(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Códec y modo broadcast sugerido (JSON, sin capturar JPEG)."""
    try:
        return await _probe_rtsp_codec_meta(body, settings)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("rtsp-probe-meta")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/rtsp/probe-meta")
async def probe_camera_rtsp_meta_alias(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    return await _probe_rtsp_codec_meta(body, settings)


@router.post("/tools/rtsp-probe")
async def probe_camera_rtsp_tools(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> Response:
    """Vista previa RTSP JPEG (legacy). Preferir /tools/rtsp-probe-json."""
    try:
        return await _probe_camera_rtsp_impl(body, settings)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("rtsp-probe tools")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/rtsp/probe")
async def probe_camera_rtsp(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> Response:
    """Alias de tools/rtsp-probe."""
    return await _probe_camera_rtsp_impl(body, settings)


@router.post("/cameras/probe")
async def probe_camera_rtsp_legacy(
    body: CameraProbeRequest,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> Response:
    """Alias retrocompatible."""
    return await _probe_camera_rtsp_impl(body, settings)


@router.get("/brands/{slug}")
async def get_brand(
    slug: str,
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    root = default_brands_dir(settings.config_dir)
    try:
        profile = load_brand_profile(slug, root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "slug": slug.strip().lower(),
        "brand": profile.brand,
        "version": profile.version,
        "models": profile.models,
        "protocols": profile.protocols.model_dump(),
    }


@router.get("/cameras/{camera_id}/rtsp-urls")
async def camera_rtsp_urls(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """URLs RTSP dispositivo y edge (vivo/playback) según marca."""
    camera = await repo.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    try:
        urls = camera.rtsp_urls_summary(settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    masked = {k: _mask_url(v) for k, v in urls.items()}
    return {
        "camera_id": camera_id,
        "brand": camera.source.brand,
        "channel": camera.source.channel,
        "urls_masked": masked,
        "gateway_path": default_gateway_path(camera, settings),
    }


class AccessInfoPreviewBody(BaseModel):
    """Vista previa de URLs de conexión (formulario «otros datos»)."""

    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    brand: str | None = None
    channel: str | None = None


def _camera_with_source_overrides(
    camera: CameraRecord, body: AccessInfoPreviewBody | None
) -> CameraRecord:
    if body is None:
        return camera
    data = camera.model_dump_for_storage()
    src = data["source"]
    changed = False
    if body.host and body.host.strip():
        src["host"] = body.host.strip()
        changed = True
    if body.port is not None:
        src["port"] = body.port
        changed = True
    if body.username is not None:
        src["username"] = body.username
        changed = True
    if body.password is not None:
        src["password"] = body.password
        changed = True
    if body.brand is not None:
        src["brand"] = body.brand.strip()
        changed = True
    if body.channel is not None and str(body.channel).strip():
        src["channel"] = str(body.channel).strip()
        changed = True
    if not changed:
        return camera
    return CameraRecord.from_storage(data)


def _is_loopback_host(host: str) -> bool:
    h = (host or "").split(":")[0].strip().lower()
    return h in ("localhost", "127.0.0.1", "::1", "[::1]")


def _cached_public_ip(request: Request) -> str:
    pip_svc = getattr(request.app.state, "public_ip_service", None)
    if pip_svc and pip_svc.get_cached():
        return pip_svc.get_cached()
    wan = getattr(request.app.state, "wan_sync_service", None)
    if wan and wan.state.public_ip.strip():
        return wan.state.public_ip.strip()
    return ""


def _resolve_panel_urls(request: Request, settings: AppSettings) -> dict[str, str]:
    from src.services.lan_ip import detect_edge_lan_ip
    """
    URLs para broadcast / WebRTC / relay vistos desde fuera.
    La IP de la cámara (RTSP origen) sigue siendo la privada en source; aquí va la IP pública del edge.
    """
    scheme = (
        (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
        .split(",")[0]
        .strip()
    )
    host_raw = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
        .split(",")[0]
        .strip()
    )
    port = settings.edge_api_port
    access_base = f"{scheme}://{host_raw}".rstrip("/") if host_raw else ""
    configured = (settings.edge_panel_public_url or "").strip().rstrip("/")
    detected = _cached_public_ip(request)
    lan = settings.ap_ip or "192.168.192.192"
    edge_lan = detect_edge_lan_ip(lan)
    lan_access_base = (
        f"{scheme}://{edge_lan}:{port}" if edge_lan else f"{scheme}://{lan}:{port}"
    )

    public_base = ""
    note = ""

    if configured:
        public_base = configured
        note = "URL fija de EDGE_PANEL_PUBLIC_URL (opcional; anula la detección automática)."
    elif detected:
        public_base = f"{scheme}://{detected}:{port}"
        lan_hint = (
            f" En esta red abre el panel por LAN: {lan_access_base}/ "
            f"(la IP pública {detected} suele no funcionar desde dentro de casa)."
        )
        if access_base and _is_loopback_host(host_raw):
            note = (
                f"IP pública detectada: {detected}. "
                f"Abres el panel por {host_raw} (túnel/local).{lan_hint} "
                f"Desde internet: reenvío puerto {port} en el router."
            )
        else:
            note = f"IP pública: {detected} (se actualiza sola).{lan_hint}"
    elif host_raw and not _is_loopback_host(host_raw):
        public_base = access_base
        note = "Sin IP pública en caché aún; usando la misma URL del navegador."
    else:
        public_base = f"{scheme}://{lan}:{port}"
        note = (
            f"Aún no hay IP pública detectada. En la LAN del edge: {public_base}. "
            "Comprueba salida a internet del guardia."
        )

    api_base = public_base or access_base or lan_access_base
    return {
        "api_base": api_base,
        "access_url": access_base,
        "public_url": public_base,
        "lan_access_url": lan_access_base,
        "edge_lan_ip": edge_lan,
        "public_ip": detected,
        "public_host": detected or edge_lan or lan,
        "url_note": note,
        "lan_ip": lan,
    }


def _build_camera_access_info(
    camera: CameraRecord,
    camera_id: str,
    *,
    panel_urls: dict[str, str],
    settings: AppSettings,
    relay_manager: RelayManager,
    gateway_manager: GatewayManager | None = None,
    preview: bool = False,
) -> dict:
    api_base = panel_urls["api_base"]
    lan_ip = panel_urls["lan_ip"]
    device_rtsp = ""
    device_rtsp_masked = ""
    try:
        device_rtsp = camera.rtsp_url(settings=settings)
        device_rtsp_masked = _mask_url(device_rtsp)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        device_rtsp_masked = f"(error: {exc})"

    pwd = camera.source.password.get_secret_value()
    mpv_device = f'mpv --rtsp-transport=tcp "{device_rtsp_masked}"'
    if device_rtsp:
        mpv_device = (
            f'mpv --profile=low-latency --rtsp-transport=tcp "{device_rtsp}"'
        )
    source_block = {
        "host": camera.source.host,
        "port": camera.source.port,
        "username": camera.source.username,
        "password": pwd,
        "channel": camera.source.channel,
        "brand": camera.source.brand or "",
        "transport": camera.source.transport or "tcp",
        "rtsp_url": device_rtsp or device_rtsp_masked,
        "mpv": mpv_device,
    }

    relay = relay_manager.get_relay(camera_id)
    listen_port = relay_manager.get_listen_port(camera_id) or camera.output.relay.listen_port
    relay_preview: dict | None = None
    try:
        _, out_url = build_relay_urls(camera, settings, listen_port)
        local_url = local_listen_url(out_url)
        ext_host = panel_urls.get("public_host") or lan_ip
        lan_url = out_url.replace("0.0.0.0", ext_host).replace("127.0.0.1", ext_host)
        relay_preview = {
            "listen_port": listen_port,
            "url_local": local_url,
            "url_lan": lan_url,
            "url_masked": _mask_url(out_url),
            "mpv": f'mpv --rtsp-transport=tcp "{lan_url}"',
            "vlc": f'vlc "{lan_url}"',
            "running": relay.is_running if relay else False,
        }
    except ValueError as exc:
        relay_preview = {"error": str(exc)}

    relay_block: dict | None = None
    if camera.output.relay.enabled and not preview:
        if relay_preview and "error" not in relay_preview:
            relay_block = {"enabled": True, **relay_preview}
        elif relay_preview:
            relay_block = {"enabled": True, **relay_preview}

    panel_url = f"{api_base}/"
    viewer_url = f"{api_base}/api/v1/webrtc/{camera_id}/viewer"
    whep_url = f"{api_base}/api/v1/webrtc/{camera_id}/offer"
    status_url = f"{api_base}/api/v1/webrtc/{camera_id}/status"
    login_url = f"{api_base}/api/v1/webui/login"
    webrtc_block = {
        "enabled": camera.output.webrtc.enabled,
        "mode": camera.output.webrtc.mode,
        "whep_offer_url": whep_url,
        "viewer_url": viewer_url,
        "panel_url": panel_url,
        "status_url": status_url,
        "human_steps": [
            "Activa broadcast WebRTC en este canal (debe poner «Ingesta OK»).",
            f"Abre el visor en otra pestaña: {viewer_url} (botón abajo; lleva tu sesión).",
            "Esa URL es solo vídeo, no el panel de configuración.",
            "Si no hay imagen, espera 10–20 s y comprueba que el búfer sube.",
            "Reenvío router: WAN TCP 8000 → IP LAN del guardia (y UDP ICE si hace falta).",
        ],
        "curl_check": (
            f"# Solo para comprobar en terminal que la API responde:\n"
            f'curl -sS -X POST "{login_url}" -H "Content-Type: application/json" '
            f'-d \'{{"username":"admin","password":"TU_PASS"}}\'\n'
            f"# Copia access_token de la respuesta y:\n"
            f'curl -sS -H "Authorization: Bearer TOKEN" "{status_url}"'
        ),
    }

    store = settings.camera_store_backend.value
    store_path = (
        str(settings.resolved_cameras_db)
        if store == "sqlite"
        else str(settings.resolved_cameras_json)
    )

    gateway_block: dict | None = None
    if settings.rtsp_gateway_enabled:
        lan_host = panel_urls.get("edge_lan_ip") or panel_urls.get("lan_ip", "").split("://")[-1].split(":")[0]
        public_host = panel_urls.get("public_ip") or ""
        urls = build_gateway_access_urls(
            camera,
            settings,
            lan_host=lan_host,
            public_host=public_host,
        )
        gw_status: dict[str, Any] = {}
        if gateway_manager is not None:
            gw_status = gateway_manager.get_status([camera])
        gw_running = bool(gw_status.get("running"))
        gateway_block: dict[str, Any] = {
            "global_enabled": True,
            "camera_enabled": camera.output.gateway.enabled,
            "running": gw_running,
            "mediamtx_binary": gw_status.get("mediamtx_binary"),
            "last_error": gw_status.get("last_error"),
            "config_path": gw_status.get("config_path"),
            "lan_host": lan_host,
            "public_host": public_host or None,
            "hints": [
                "Usa estas URLs desde cualquier PC en la LAN (o IP pública + reenvío puerto "
                f"WAN:{settings.rtsp_gateway_wan_port} → edge:{settings.rtsp_gateway_port}).",
                "Vivo: pull directo al gateway. Playback: búfer reciente + cola en vivo "
                "(starttime/endtime en la query).",
                "Activa broadcast o ingesta gateway para que el búfer tenga datos.",
            ],
        }
        if "error" in urls:
            gateway_block["config_error"] = urls["error"]
        else:
            gateway_block.update(urls)
        if not gw_running and gw_status.get("last_error"):
            gateway_block["hints"].insert(
                0,
                f"MediaMTX no está en marcha: {gw_status['last_error']}",
            )
        elif not gw_running and not gw_status.get("mediamtx_binary"):
            gateway_block["hints"].insert(
                0,
                "Instala MediaMTX (scripts/install.sh o apt) y define MEDIAMTX_BINARY en .env.",
            )
        elif not camera.output.gateway.enabled:
            gateway_block["hints"].insert(
                0,
                "Activa output.gateway.enabled en esta cámara y POST /api/v1/gateway/reload "
                "para registrar las rutas en MediaMTX.",
            )
        elif not gw_running:
            gateway_block["hints"].insert(
                0,
                "Reinicia el servicio o POST /api/v1/gateway/reload. Si el relay usaba el "
                f"puerto {settings.rtsp_gateway_port}, desactívalo (el gateway lo sustituye).",
            )

    out: dict = {
        "camera_id": camera_id,
        "label": camera.label,
        "storage": {"backend": store, "path": store_path},
        "source": source_block,
        "device_rtsp_masked": device_rtsp_masked,
        "relay": relay_block,
        "relay_preview": relay_preview,
        "gateway": gateway_block,
        "webrtc": webrtc_block,
        "panel_urls": {
            "public": panel_urls.get("public_url") or api_base,
            "lan": panel_urls.get("lan_access_url") or "",
            "edge_lan_ip": panel_urls.get("edge_lan_ip") or "",
            "public_ip": panel_urls.get("public_ip") or "",
            "access": panel_urls.get("access_url") or "",
            "note": panel_urls.get("url_note") or "",
        },
        "broadcast_status_url": f"{api_base}/api/v1/cameras/{camera_id}/broadcast/status",
    }
    if preview:
        out["preview"] = True
    return out


@router.get("/cameras/{camera_id}/access-info")
async def camera_access_info(
    camera_id: str,
    request: Request,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
) -> dict:
    """Datos de conexión para pruebas (RTSP cámara, relay, WebRTC)."""
    camera = await repo.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    panel_urls = _resolve_panel_urls(request, settings)
    return _build_camera_access_info(
        camera,
        camera_id,
        panel_urls=panel_urls,
        settings=settings,
        relay_manager=relay_manager,
        gateway_manager=gateway_manager,
    )


@router.post("/cameras/{camera_id}/access-info/preview")
async def camera_access_info_preview(
    camera_id: str,
    body: AccessInfoPreviewBody,
    request: Request,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
) -> dict:
    """Vista previa de URLs con IP/credenciales del formulario (sin guardar)."""
    camera = await repo.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    preview_cam = _camera_with_source_overrides(camera, body)
    panel_urls = _resolve_panel_urls(request, settings)
    return _build_camera_access_info(
        preview_cam,
        camera_id,
        panel_urls=panel_urls,
        settings=settings,
        relay_manager=relay_manager,
        gateway_manager=gateway_manager,
        preview=True,
    )


@router.get("/cameras")
async def list_cameras(
    repo: Annotated[CameraRepository, Depends(get_repository)],
) -> list[dict]:
    cameras = await repo.list_all()
    return [c.model_dump_for_storage() for c in cameras]


@router.post("/cameras", status_code=201)
async def create_camera(
    body: CameraCreatePayload,
    repo: Annotated[CameraRepository, Depends(get_repository)],
) -> dict:
    record = body.to_record()
    try:
        created = await repo.create(record)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return created.model_dump_for_storage()


@router.put("/cameras/{camera_id}")
async def update_camera(
    camera_id: str,
    body: CameraCreatePayload,
    repo: Annotated[CameraRepository, Depends(get_repository)],
) -> dict:
    existing = await repo.get(camera_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    incoming = body.to_record()
    data = existing.model_dump_for_storage()
    patch = incoming.model_dump_for_storage()
    patch["camera_id"] = camera_id
    for key in ("source", "buffer"):
        if patch.get(key):
            merged = {**data.get(key, {}), **patch[key]}
            if key == "source" and not str(merged.get("password") or "").strip():
                merged.pop("password", None)
            data[key] = merged
    if patch.get("output"):
        existing_out = data.get("output", {})
        patch_out = patch["output"]
        merged_out = {**existing_out, **patch_out}
        for sub in ("relay", "webrtc", "gateway"):
            if sub in patch_out:
                merged_out[sub] = {**existing_out.get(sub, {}), **patch_out[sub]}
        data["output"] = merged_out
    for key in ("enabled", "label"):
        if key in patch:
            data[key] = patch[key]
    record = CameraRecord.from_storage(data)
    try:
        updated = await repo.update(record)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return updated.model_dump_for_storage()


@router.delete("/cameras/{camera_id}", status_code=204)
async def delete_camera(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
) -> None:
    if not await repo.delete(camera_id):
        raise HTTPException(status_code=404, detail="Cámara no encontrada")


@router.get("/stream/{camera_id}")
async def stream_clip(
    camera_id: str,
    dispatcher: Annotated[VideoDispatcher, Depends(get_dispatcher)],
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
    pre_seconds: float | None = Query(default=None, ge=0.1, le=120),
    post_seconds: float | None = Query(default=None, ge=0, le=120),
) -> StreamingResponse:
    """Clip de evento nube: pre-alarma + post en vivo."""
    require_operating_now(schedule_svc)
    try:
        generator = dispatcher.stream_event_clip(
            camera_id, pre_seconds=pre_seconds, post_seconds=post_seconds
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        generator,
        media_type="application/octet-stream",
        headers={
            "X-Kanvis-Stream-Format": "kanv1-length-prefixed-h264",
            "X-Kanvis-Stream-Mode": "event-clip",
            "Cache-Control": "no-store",
        },
    )


@router.get("/playback/{camera_id}")
async def playback(
    camera_id: str,
    dispatcher: Annotated[VideoDispatcher, Depends(get_dispatcher)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
    offset_sec: float | None = Query(default=None, ge=0.1, le=300),
    duration_sec: float | None = Query(default=None, ge=0.1, le=300),
    live_tail: bool = Query(default=False, description="Seguir con vídeo en vivo"),
) -> StreamingResponse:
    """
    Playback desde (ahora - offset_sec).
    Ejemplo prueba 3s: ?offset_sec=3
    Ejemplo nube 6s: ?offset_sec=6
    """
    require_operating_now(schedule_svc)
    try:
        camera = manager.get_camera_record(camera_id)
        if camera is None:
            raise KeyError(camera_id)
        offset = (
            offset_sec
            if offset_sec is not None
            else camera.buffer.default_playback_offset_sec
        )
        generator = dispatcher.stream_playback(
            camera_id,
            offset_sec=offset,
            duration_sec=duration_sec,
            include_live_tail=live_tail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        generator,
        media_type="application/octet-stream",
        headers={
            "X-Kanvis-Stream-Format": "kanv1-length-prefixed-h264",
            "X-Kanvis-Stream-Mode": "playback",
            "Cache-Control": "no-store",
        },
    )


@router.get("/cameras/{camera_id}/status")
async def camera_status(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
    webrtc_manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    broadcast_ingest_active = manager.is_broadcast_ingest_active(camera_id)
    consumer = manager.get_consumer(camera_id)
    buffer = manager.get_buffer(camera_id)
    camera = await repo.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")

    def _ingest_hint(ingest: dict, span: float) -> str | None:
        if not broadcast_ingest_active:
            return "Activa broadcast para que el edge lea RTSP de la cámara y rellene el búfer."
        if not ingest.get("connected") or span < 0.35:
            err = ingest.get("last_error") or "no conecta a la URL RTSP"
            return (
                f"Broadcast ON pero sin vídeo en búfer. El edge sí intenta ingesta; "
                f"revisa IP/canal/marca (prueba «Probar conexión»). Error: {err}"
            )
        return None

    if not consumer or not buffer:
        ingest_snap: dict = {}
        span = 0.0
        return {
            "camera_id": camera_id,
            "label": camera.label,
            "broadcast_ingest_active": broadcast_ingest_active,
            "ingest": {**ingest_snap, "connected": False},
            "buffer_packets": 0,
            "buffer_span_seconds": span,
            "buffer_max_duration_seconds": camera.effective_buffer_duration(
                settings.buffer_duration_seconds
            ),
            "consumer_alive": False,
            "ingest_hint": _ingest_hint(ingest_snap, span),
            "output_protocol": camera.output.protocol.value,
            "webrtc": {"enabled": camera.output.webrtc.enabled},
        }
    relay = relay_manager.get_relay(camera_id)
    relay_info: dict | None = None
    if camera.output.relay.enabled:
        try:
            _, out_url = build_relay_urls(camera, settings)
            relay_info = {
                "configured": True,
                "running": relay.is_running if relay else False,
                "output_url_masked": _mask_url(out_url),
                "status": relay.get_status() if relay else None,
            }
        except ValueError as exc:
            relay_info = {"configured": True, "error": str(exc)}
    gateway_info: dict | None = None
    if camera.output.gateway.enabled:
        gateway_info = {
            "configured": True,
            "access_mode": camera.output.gateway.access_mode.value,
            "url_local": build_gateway_client_url(camera, settings),
            "running": gateway_manager.is_enabled and gateway_manager.get_status().get("running"),
        }
    elif camera.output.gateway.access_mode == ExternalAccessMode.DIRECT:
        gateway_info = {
            "configured": False,
            "access_mode": "direct",
            "hint": "Port forwarding WAN → cámara:554 (sin proxy edge)",
            "source_url_masked": _mask_url(camera.rtsp_url(settings=settings)),
        }
    rtsp_urls: dict[str, str] | None = None
    try:
        rtsp_urls = {
            k: _mask_url(v)
            for k, v in camera.rtsp_urls_summary(settings).items()
        }
    except (FileNotFoundError, ValueError, KeyError):
        rtsp_urls = None
    ingest_snap = consumer.metrics.snapshot()
    span = round(buffer.span_seconds(), 2)
    return {
        "camera_id": camera_id,
        "label": camera.label,
        "brand": camera.source.brand or None,
        "channel": camera.source.channel,
        "source_host": camera.source.host,
        "source_rtsp_url_masked": _mask_url(camera.rtsp_url(settings=settings)),
        "rtsp_urls_masked": rtsp_urls,
        "broadcast_ingest_active": broadcast_ingest_active,
        "ingest_hint": _ingest_hint(ingest_snap, span),
        "ingest": ingest_snap,
        "buffer_packets": buffer.size(),
        "buffer_span_seconds": span,
        "buffer_max_duration_seconds": buffer.max_duration_seconds,
        "buffer_packets_max": settings.buffer_max_packets_safety,
        "consumer_alive": consumer.is_running,
        "output_protocol": camera.output.protocol.value,
        "relay": relay_info,
        "gateway": gateway_info,
        "webrtc": {
            "enabled": camera.output.webrtc.enabled,
            "mode": camera.output.webrtc.mode,
            "session": (
                {
                    "mode": s.mode,
                    "connection_state": s.connection_state,
                    "ice_state": s.ice_state,
                }
                if (pub := webrtc_manager.get_publisher(camera_id))
                and (s := pub.get_state())
                else None
            ),
        },
    }


@router.post("/discovery/scan")
async def discovery_scan(
    request: Request,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Escaneo manual RTSP/ONVIF (una pasada) y autoprovisión en inventario."""
    scanner = NetworkScanner(settings, repo)
    devices = await scanner.run_discovery_pass()
    added = await scanner.provision_discovered(devices)
    return {
        "discovered": len(devices),
        "provisioned_new": added,
        "devices": [
            {"ip": d.ip_address, "port": d.rtsp_port, "source": d.source}
            for d in devices
        ],
    }


@router.get("/relays")
async def list_relays(
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> list[dict]:
    return relay_manager.list_status()


@router.get("/gateway/status")
async def gateway_status(
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    """Estado del proxy RTSP unificado (MediaMTX)."""
    cameras = await repo.list_all()
    status = gateway_manager.get_status(cameras)
    status["install_hint"] = (
        "WAN:" + str(settings.rtsp_gateway_wan_port)
        + " → IP_EDGE:" + str(settings.rtsp_gateway_port)
        + "/TCP; ver docs/RTSP_GATEWAY.md"
    )
    status["cameras_with_gateway"] = sum(
        1 for c in cameras if c.enabled and c.output.gateway.enabled
    )
    status["diagnosis"] = _gateway_diagnosis(status, settings)
    return status


def _gateway_diagnosis(status: dict, settings: AppSettings) -> list[str]:
    hints: list[str] = []
    if not settings.rtsp_gateway_enabled:
        hints.append("RTSP_GATEWAY_ENABLED=false en /etc/kanvis-edge/env")
        return hints
    if not status.get("mediamtx_binary"):
        hints.append("MediaMTX no encontrado: instala mediamtx o MEDIAMTX_BINARY")
    if status.get("cameras_with_gateway", 0) == 0:
        hints.append(
            "Ninguna cámara con output.gateway.enabled=true; guarda la cámara con "
            "broadcast (o edita cameras.json) y POST /api/v1/gateway/reload"
        )
    if status.get("last_error"):
        hints.append(str(status["last_error"]))
    if not status.get("running") and not hints:
        hints.append(
            f"Puerto {settings.rtsp_gateway_port} sin listener; revisa journalctl -u kanvis-edge"
        )
    return hints


@router.post("/gateway/reload")
async def gateway_reload(
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
    repo: Annotated[CameraRepository, Depends(get_repository)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    if not settings.rtsp_gateway_enabled:
        raise HTTPException(status_code=400, detail="RTSP_GATEWAY_ENABLED=false")
    await gateway_manager.sync_from_repository()
    cameras = await repo.list_all()
    status = gateway_manager.get_status(cameras)
    status["cameras_with_gateway"] = sum(
        1 for c in cameras if c.enabled and c.output.gateway.enabled
    )
    status["diagnosis"] = _gateway_diagnosis(status, settings)
    return status


@router.post("/cameras/{camera_id}/relay/start")
async def relay_start(
    camera_id: str,
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    consumer_manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    repo: Annotated[CameraRepository, Depends(get_repository)],
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
) -> dict:
    require_operating_now(schedule_svc)
    cam = await repo.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if not cam.output.relay.enabled:
        raise HTTPException(status_code=400, detail="relay.enabled=false en configuración")
    loop = asyncio.get_running_loop()
    await consumer_manager.set_broadcast_ingest(camera_id, True, loop)
    await relay_manager.sync_from_repository()
    if not relay_manager.start_camera(camera_id):
        raise HTTPException(status_code=404, detail="Relay no disponible para esta cámara")
    relay = relay_manager.get_relay(camera_id)
    return relay.get_status() if relay else {"started": True}


@router.post("/cameras/{camera_id}/relay/stop")
async def relay_stop(
    camera_id: str,
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> dict:
    if not relay_manager.stop_camera(camera_id):
        raise HTTPException(status_code=404, detail="Relay no activo")
    return {"stopped": True, "camera_id": camera_id}


@router.get("/cameras/{camera_id}/relay/status")
async def relay_status(
    camera_id: str,
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> dict:
    relay = relay_manager.get_relay(camera_id)
    if not relay:
        raise HTTPException(status_code=404, detail="Relay no configurado o no activo")
    return relay.get_status()
