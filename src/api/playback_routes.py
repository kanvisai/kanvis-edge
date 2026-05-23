"""Playback RTSP interno (MediaMTX runOnDemand → FFmpeg → este endpoint)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.status import HTTP_403_FORBIDDEN

from src.config_loader import AppSettings, get_settings
from src.discovery.repository import CameraRepository
from src.ingestion.consumer import StreamConsumerManager
from src.playback.parse import parse_playback_query_string, playback_query_from_request
from src.playback.resolver import brand_profile_for_camera, find_camera_for_gateway_path
from src.playback.stream import PlaybackStreamError, stream_playback_h264
from src.schedule.service import OperatingScheduleService, require_operating_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal-playback"])


def _is_loopback(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    host = (client.host or "").strip()
    return host in ("127.0.0.1", "::1", "localhost")


def get_repository(request: Request) -> CameraRepository:
    return request.app.state.camera_repository


def get_consumer_manager(request: Request) -> StreamConsumerManager:
    return request.app.state.consumer_manager


def get_schedule_service(request: Request) -> OperatingScheduleService:
    return request.app.state.operating_schedule_service


@router.get("/rtsp-playback")
async def internal_rtsp_playback(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    repository: Annotated[CameraRepository, Depends(get_repository)],
    manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    schedule_svc: Annotated[OperatingScheduleService, Depends(get_schedule_service)],
    mtx_path: Annotated[str, Query(description="MTX_PATH de MediaMTX")],
    mtx_query: Annotated[str, Query(description="MTX_QUERY (starttime/endtime)")] = "",
) -> StreamingResponse:
    """
    Stream H.264 Annex-B para FFmpeg (runOnDemand de MediaMTX).

    Solo loopback. Sirve búfer reciente + cola en vivo + playback de cámara si aplica.
    """
    if not _is_loopback(request):
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Solo accesible desde localhost (MediaMTX runOnDemand)",
        )
    require_operating_now(schedule_svc)

    cameras = await repository.list_all()
    camera = find_camera_for_gateway_path(mtx_path, cameras, settings)
    if camera is None:
        raise HTTPException(status_code=404, detail=f"Ruta RTSP desconocida: {mtx_path}")

    profile = brand_profile_for_camera(camera, settings)
    query_raw = playback_query_from_request(request, mtx_query)
    try:
        start, end = parse_playback_query_string(query_raw, profile=profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    consumer = manager.get_consumer(camera.camera_id)
    buffer = manager.get_buffer(camera.camera_id)
    if consumer is None or buffer is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Ingesta/búfer no activos para esta cámara; activa broadcast o "
                "gateway con ingesta automática"
            ),
        )

    depth = camera.effective_buffer_duration(settings.buffer_duration_seconds)
    from src.ingestion.packet_decode import trim_packets_from_keyframe
    from src.playback.window import plan_playback_window

    try:
        plan = plan_playback_window(
            start=start, end=end, buffer_depth_sec=depth
        )
        if plan.needs_buffer:
            packets = trim_packets_from_keyframe(
                buffer.snapshot_between_ages(
                    plan.buffer_start_sec_ago,
                    plan.buffer_end_sec_ago,
                )
            )
            if not packets:
                raise PlaybackStreamError(
                    f"Búfer insuficiente para {plan.buffer_start_sec_ago:.1f}s; "
                    "activa ingesta y espera a que se llene"
                )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlaybackStreamError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def _body():
        try:
            async for chunk in stream_playback_h264(
                camera=camera,
                consumer=consumer,
                buffer=buffer,
                settings=settings,
                start=start,
                end=end,
            ):
                yield chunk
        except PlaybackStreamError as exc:
            logger.warning("Playback RTSP %s: %s", camera.camera_id, exc)
            return

    return StreamingResponse(
        _body(),
        media_type="video/h264",
        headers={
            "Cache-Control": "no-store",
            "X-Kanvis-Playback-Camera": camera.camera_id,
        },
    )
