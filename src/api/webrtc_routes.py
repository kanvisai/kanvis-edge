"""Rutas WebRTC (WHEP visor local, WHIP push, rewind)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.webrtc.manager import WebRtcManager

router = APIRouter(prefix="/api/v1/webrtc", tags=["webrtc"])


class SdpPayload(BaseModel):
    sdp: str
    type: str = Field(default="offer", description="offer | answer")


def get_webrtc_manager(request: Request) -> WebRtcManager:
    return request.app.state.webrtc_manager


@router.get("")
async def list_webrtc_sessions(
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> list[dict]:
    return manager.list_status()


@router.post("/{camera_id}/offer")
async def whep_offer(
    camera_id: str,
    body: SdpPayload,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> SdpPayload:
    """
    WHEP-like: el navegador/cliente envía SDP offer; el edge responde answer
    con vídeo desde el búfer (permite rewind previo).
    """
    try:
        answer = await manager.handle_whep_offer(camera_id, body.sdp, body.type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
        "whip_url": state.whip_url,
        "error": state.error,
    }


@router.delete("/{camera_id}", status_code=204)
async def webrtc_close(
    camera_id: str,
    manager: Annotated[WebRtcManager, Depends(get_webrtc_manager)],
) -> None:
    if not await manager.close_session(camera_id):
        raise HTTPException(status_code=404, detail="Sin sesión WebRTC activa")
