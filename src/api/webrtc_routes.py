"""Rutas WebRTC (WHEP visor local, WHIP push, rewind)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.ingestion.consumer import StreamConsumerManager
from src.relay.manager import RelayManager
from src.webrtc.manager import WebRtcManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webrtc", tags=["webrtc"])

_VIEWER_HTML = (
    Path(__file__).resolve().parent.parent / "web" / "static" / "webrtc-viewer.html"
)


def _webrtc_http_error(exc: Exception, camera_id: str) -> HTTPException:
    msg = str(exc).strip() or type(exc).__name__
    low = msg.lower()
    if "sin ingesta" in low or "no activa" in low:
        return HTTPException(
            status_code=503,
            detail="Ingesta no lista; activa broadcast y espera unos segundos",
        )
    if "bundle" in low or "policy" in low:
        return HTTPException(
            status_code=400,
            detail=f"Negociación WebRTC fallida (SDP/bundle): {msg}",
        )
    logger.exception("WHEP offer falló para %s", camera_id)
    return HTTPException(status_code=500, detail=f"Error WebRTC: {msg}")


class SdpPayload(BaseModel):
    sdp: str
    type: str = Field(default="offer", description="offer | answer")


def get_webrtc_manager(request: Request) -> WebRtcManager:
    return request.app.state.webrtc_manager


def get_consumer_manager(request: Request) -> StreamConsumerManager:
    return request.app.state.consumer_manager


def get_relay_manager(request: Request) -> RelayManager:
    return request.app.state.relay_manager


@router.get("")
async def list_webrtc_sessions(
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> list[dict]:
    return manager.list_status()


@router.get("/{camera_id}/viewer")
async def webrtc_viewer_page(camera_id: str) -> FileResponse:
    """
    Página mínima solo para ver WebRTC (nueva pestaña).
    Usa ?token= del panel o login integrado. Requiere broadcast activo.
    """
    if not _VIEWER_HTML.is_file():
        raise HTTPException(status_code=404, detail="Visor WebRTC no instalado")
    return FileResponse(
        _VIEWER_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{camera_id}/offer")
async def whep_offer(
    camera_id: str,
    body: SdpPayload,
    request: Request,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
    consumer_manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
) -> SdpPayload:
    """
    WHEP-like: el navegador/cliente envía SDP offer; el edge responde answer
    con vídeo desde el búfer (permite rewind previo).
    """
    loop = asyncio.get_running_loop()
    await consumer_manager.set_broadcast_ingest(camera_id, True, loop)
    try:
        answer = await manager.handle_whep_offer(camera_id, body.sdp, body.type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _webrtc_http_error(exc, camera_id) from exc
    return SdpPayload(sdp=answer["sdp"], type=answer["type"])


@router.post("/{camera_id}/whip")
async def whip_connect(
    camera_id: str,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> dict:
    """WHIP: publica el stream hacia webrtc.signaling_url."""
    try:
        return await manager.start_whip(camera_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{camera_id}/rewind")
async def webrtc_rewind(
    camera_id: str,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
    offset_sec: float | None = Query(
        default=None,
        ge=0.1,
        le=120,
        description="Segundos hacia atrás (default: webrtc.rewind_offset_sec, ej. 3)",
    ),
) -> dict:
    """Reproduce en la sesión WebRTC activa desde (ahora - offset_sec)."""
    try:
        return await manager.rewind(camera_id, offset_sec)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{camera_id}/status")
async def webrtc_status(
    camera_id: str,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> dict:
    pub = manager.get_publisher(camera_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Sin sesión WebRTC activa")
    state = pub.get_state()
    return {
        "camera_id": state.camera_id,
        "mode": state.mode,
        "connection_state": state.connection_state,
        "ice_state": state.ice_state,
        "rewind_packets_pending": state.rewind_packets_pending,
        "frames_sent": state.frames_sent,
        "decode_failures": state.decode_failures,
        "video_source": state.video_source,
        "whip_url": state.whip_url,
        "error": state.error,
    }


@router.delete("/{camera_id}", status_code=204)
async def webrtc_close(
    camera_id: str,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
    consumer_manager: Annotated[StreamConsumerManager, Depends(get_consumer_manager)],
    relay_manager: Annotated[RelayManager, Depends(get_relay_manager)],
) -> None:
    if not await manager.close_session(camera_id):
        raise HTTPException(status_code=404, detail="Sin sesión WebRTC activa")
    relay = relay_manager.get_relay(camera_id)
    if not (relay and relay.is_running):
        loop = asyncio.get_running_loop()
        await consumer_manager.set_broadcast_ingest(camera_id, False, loop)
