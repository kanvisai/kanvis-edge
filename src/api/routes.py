"""Rutas REST del Gateway."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.api.dispatcher import VideoDispatcher
from src.config_loader import AppSettings, get_settings
from src.discovery.models import CameraCreatePayload, CameraRecord
from src.discovery.rtsp_urls import default_gateway_path
from src.discovery.repository import CameraRepository
from src.ingestion.consumer import StreamConsumerManager
from src.discovery.scanner import NetworkScanner
from src.gateway.config import build_gateway_client_url
from src.gateway.manager import GatewayManager
from src.relay.manager import RelayManager
from src.webrtc.manager import WebRtcManager
from src.relay.worker import _mask_url, build_relay_urls
from src.discovery.models import ExternalAccessMode
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


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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


@router.get("/connectivity/status")
async def connectivity_status(
    settings: Annotated[AppSettings, Depends(get_app_settings)],
    wan: Annotated[WanSyncService | None, Depends(get_wan_sync)],
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

    if wan is None:
        base["state"] = None
        base["message"] = "WAN sync no activo (activa DDNS_ENABLED o CLOUD_REPORT_ENABLED)"
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
                }
            )
        except (FileNotFoundError, ValueError):
            continue
    return {"brands_dir": str(root), "brands": items}


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
    for key in ("source", "output", "buffer"):
        if patch.get(key):
            data[key] = {**data.get(key, {}), **patch[key]}
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
    pre_seconds: float | None = Query(default=None, ge=0.1, le=120),
    post_seconds: float | None = Query(default=None, ge=0, le=120),
) -> StreamingResponse:
    """Clip de evento nube: pre-alarma + post en vivo."""
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
    offset_sec: float | None = Query(default=None, ge=0.1, le=300),
    duration_sec: float | None = Query(default=None, ge=0.1, le=300),
    live_tail: bool = Query(default=False, description="Seguir con vídeo en vivo"),
) -> StreamingResponse:
    """
    Playback desde (ahora - offset_sec).
    Ejemplo prueba 3s: ?offset_sec=3
    Ejemplo nube 6s: ?offset_sec=6
    """
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
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
    webrtc_manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    consumer = manager.get_consumer(camera_id)
    buffer = manager.get_buffer(camera_id)
    camera = manager.get_camera_record(camera_id)
    if not consumer or not buffer or not camera:
        raise HTTPException(status_code=404, detail="Cámara no activa")
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
    return {
        "camera_id": camera_id,
        "label": camera.label,
        "brand": camera.source.brand or None,
        "channel": camera.source.channel,
        "source_host": camera.source.host,
        "source_rtsp_url_masked": _mask_url(camera.rtsp_url(settings=settings)),
        "rtsp_urls_masked": rtsp_urls,
        "ingest": consumer.metrics.snapshot(),
        "buffer_packets": buffer.size(),
        "buffer_span_seconds": round(buffer.span_seconds(), 2),
        "buffer_max_duration_seconds": buffer.max_duration_seconds,
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
    cameras = await repo.list_cameras()
    status = gateway_manager.get_status(cameras)
    status["install_hint"] = (
        "WAN:" + str(settings.rtsp_gateway_wan_port)
        + " → IP_EDGE:" + str(settings.rtsp_gateway_port)
        + "/TCP; ver docs/RTSP_GATEWAY.md"
    )
    return status


@router.post("/gateway/reload")
async def gateway_reload(
    gateway_manager: Annotated[GatewayManager, Depends(get_gateway_manager)],
    settings: Annotated[AppSettings, Depends(get_app_settings)],
) -> dict:
    if not settings.rtsp_gateway_enabled:
        raise HTTPException(status_code=400, detail="RTSP_GATEWAY_ENABLED=false")
    await gateway_manager.sync_from_repository()
    return gateway_manager.get_status()


@router.post("/cameras/{camera_id}/relay/start")
async def relay_start(
    camera_id: str,
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    repo: Annotated[CameraRepository, Depends(get_repository)],
) -> dict:
    cam = await repo.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if not cam.output.relay.enabled:
        raise HTTPException(status_code=400, detail="relay.enabled=false en configuración")
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
