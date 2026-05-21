"""Rutas de prueba Fase 3: snapshots, broadcast y test playback."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src.api.dispatcher import VideoDispatcher
from src.config_loader import AppSettings
from src.discovery.repository import CameraRepository
from src.ingestion.consumer import StreamConsumerManager
from src.relay.manager import RelayManager
from src.relay.worker import build_relay_urls
from src.testing.snapshot import local_listen_url
from src.testing.snapshot import SnapshotError, capture_jpeg_from_rtsp

router = APIRouter(prefix="/api/v1", tags=["testing"])


class TestPlaybackRequest(BaseModel):
    offset_sec: float = Field(default=3.0, ge=0.1, le=120)
    duration_sec: float | None = Field(default=None, ge=0.1, le=120)
    live_tail: bool = False


def get_repository(request: Request) -> CameraRepository:
    return request.app.state.camera_repository


def get_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_consumer_manager(request: Request) -> StreamConsumerManager:
    return request.app.state.consumer_manager


def get_relay_manager(request: Request) -> RelayManager:
    return request.app.state.relay_manager


def get_dispatcher(request: Request) -> VideoDispatcher:
    return request.app.state.video_dispatcher


async def _resolve_camera(
    camera_id: str,
    repo: CameraRepository,
    manager: StreamConsumerManager,
):
    camera = await repo.get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if not camera.enabled:
        raise HTTPException(status_code=400, detail="Cámara deshabilitada (enabled=false)")
    active = manager.get_camera_record(camera_id)
    if active is None:
        raise HTTPException(
            status_code=503,
            detail="Ingesta no activa; espera a que el consumidor conecte o revisa RTSP",
        )
    return camera, active


@router.get("/cameras/{camera_id}/snapshot/source")
async def snapshot_source(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    settings: Annotated[AppSettings, Depends(get_settings)],
    timeout_sec: float | None = Query(default=None, ge=3.0, le=60.0),
) -> Response:
    """Frame JPEG del RTSP original (cámara en LAN)."""
    camera, _ = await _resolve_camera(camera_id, repo, manager)
    url = camera.rtsp_url()
    tout = timeout_sec or settings.snapshot_timeout_sec
    try:
        jpeg = await capture_jpeg_from_rtsp(
            url,
            ffmpeg_path=settings.ffmpeg_path,
            transport=camera.source.transport,
            timeout_sec=tout,
        )
    except SnapshotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "X-Kanvis-Snapshot-Source": "rtsp-origin",
            "Cache-Control": "no-store",
        },
    )


@router.get("/cameras/{camera_id}/snapshot/relay")
async def snapshot_relay(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    settings: Annotated[AppSettings, Depends(get_settings)],
    timeout_sec: float | None = Query(default=None, ge=3.0, le=60.0),
) -> Response:
    """Frame JPEG del RTSP rebroadcast (relay debe estar en marcha)."""
    camera, _ = await _resolve_camera(camera_id, repo, manager)
    if not camera.output.relay.enabled:
        raise HTTPException(
            status_code=400,
            detail="relay.enabled=false; activa relay en configuración",
        )
    relay = relay_manager.get_relay(camera_id)
    if not relay or not relay.is_running:
        raise HTTPException(
            status_code=503,
            detail="Relay no está activo; POST /cameras/{id}/broadcast/start primero",
        )
    port = relay_manager.get_listen_port(camera_id)
    try:
        _, out_url = build_relay_urls(camera, settings, port)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    local_url = local_listen_url(out_url)
    tout = timeout_sec or settings.snapshot_timeout_sec
    try:
        jpeg = await capture_jpeg_from_rtsp(
            local_url,
            ffmpeg_path=settings.ffmpeg_path,
            transport="tcp",
            timeout_sec=tout,
        )
    except SnapshotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "X-Kanvis-Snapshot-Source": "rtsp-relay",
            "Cache-Control": "no-store",
        },
    )


@router.post("/cameras/{camera_id}/broadcast/start")
async def broadcast_start(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> dict:
    """Inicia rebroadcast RTSP (alias de relay/start para la UI de pruebas)."""
    cam = await repo.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    if not cam.output.relay.enabled:
        raise HTTPException(
            status_code=400,
            detail="Activa output.relay.enabled en cameras.json antes de broadcast",
        )
    await relay_manager.sync_from_repository()
    if not relay_manager.start_camera(camera_id):
        raise HTTPException(
            status_code=503,
            detail="No se pudo iniciar relay; revisa ffmpeg y logs",
        )
    relay = relay_manager.get_relay(camera_id)
    status = relay.get_status() if relay else {"started": True}
    return {"broadcast": "started", "camera_id": camera_id, "relay": status}


@router.post("/cameras/{camera_id}/broadcast/stop")
async def broadcast_stop(
    camera_id: str,
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> dict:
    """Detiene rebroadcast RTSP."""
    if not relay_manager.stop_camera(camera_id):
        raise HTTPException(status_code=404, detail="Broadcast/relay no activo")
    return {"broadcast": "stopped", "camera_id": camera_id}


@router.get("/cameras/{camera_id}/broadcast/status")
async def broadcast_status(
    camera_id: str,
    repo: Annotated[CameraRepository, Depends(get_repository)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
    settings: Annotated[AppSettings, Depends(get_settings)],
) -> dict:
    """Estado del rebroadcast y URLs útiles para pruebas."""
    cam = await repo.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Cámara no encontrada")
    relay = relay_manager.get_relay(camera_id)
    out: dict = {
        "camera_id": camera_id,
        "relay_enabled": cam.output.relay.enabled,
        "running": relay.is_running if relay else False,
        "relay_status": relay.get_status() if relay else None,
    }
    if cam.output.relay.enabled:
        try:
            port = relay_manager.get_listen_port(camera_id)
            _, url = build_relay_urls(cam, settings, port)
            out["relay_url_local"] = local_listen_url(url)
        except ValueError as exc:
            out["config_error"] = str(exc)
    return out


@router.post("/cameras/{camera_id}/test/playback")
async def test_playback(
    camera_id: str,
    body: TestPlaybackRequest,
    dispatcher: Annotated[VideoDispatcher, Depends(get_dispatcher)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    settings: Annotated[AppSettings, Depends(get_settings)],
) -> dict:
    """
    Inicia prueba de playback: devuelve metadatos y URL para descargar el stream.
    Usa offset desde el momento de la petición (vía búfer RAM).
    """
    camera = manager.get_camera_record(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Cámara no activa")
    buffer = manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="Sin búfer")
    offset = body.offset_sec
    if offset > camera.buffer.duration_seconds:
        raise HTTPException(
            status_code=400,
            detail=f"offset_sec supera búfer ({camera.buffer.duration_seconds}s)",
        )
    span = buffer.span_seconds()
    return {
        "camera_id": camera_id,
        "test": "playback",
        "offset_sec": offset,
        "duration_sec": body.duration_sec,
        "live_tail": body.live_tail,
        "buffer_span_seconds": round(span, 2),
        "buffer_packets": buffer.size(),
        "download_url": (
            f"/api/v1/playback/{camera_id}"
            f"?offset_sec={offset}"
            + (f"&duration_sec={body.duration_sec}" if body.duration_sec else "")
            + ("&live_tail=true" if body.live_tail else "")
        ),
        "hint": "GET download_url con header X-API-Key para recibir el stream binario KANV1",
    }


@router.get("/cameras/{camera_id}/test/playback/stream")
async def test_playback_stream(
    camera_id: str,
    dispatcher: Annotated[VideoDispatcher, Depends(get_dispatcher)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    settings: Annotated[AppSettings, Depends(get_settings)],
    offset_sec: float = Query(default=3.0, ge=0.1, le=120),
    duration_sec: float | None = Query(default=None, ge=0.1, le=120),
    live_tail: bool = Query(default=False),
) -> StreamingResponse:
    """Stream directo de prueba playback (mismo formato KANV1 que /playback)."""
    camera = manager.get_camera_record(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Cámara no activa")
    if offset_sec is None:
        offset_sec = settings.default_playback_test_offset_sec
    try:
        generator = dispatcher.stream_playback(
            camera_id,
            offset_sec=offset_sec,
            duration_sec=duration_sec,
            include_live_tail=live_tail,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(
        generator,
        media_type="application/octet-stream",
        headers={
            "X-Kanvis-Stream-Format": "kanv1-length-prefixed-h264",
            "X-Kanvis-Stream-Mode": "test-playback",
            "Cache-Control": "no-store",
        },
    )
