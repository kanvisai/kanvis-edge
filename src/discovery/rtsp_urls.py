"""Construcción de URLs RTSP según perfil de marca o ruta legacy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from src.brands import (
    build_rtsp_template_values,
    load_brand_profile,
    profile_matches_model,
    render_rtsp_url,
    rtsp_path_from_url,
)
from src.brands.registry import default_brands_dir

if TYPE_CHECKING:
    from src.config_loader import AppSettings
    from src.discovery.models import CameraRecord, CameraSource


RtspTarget = Literal["device", "edge"]
RtspMode = Literal["stream", "playback"]


def _edge_rtsp_host_port(settings: AppSettings) -> tuple[str, int]:
    if settings.rtsp_gateway_enabled:
        host = settings.rtsp_gateway_listen_host
        port = settings.rtsp_gateway_port
    else:
        host = settings.edge_rtsp_host
        port = settings.edge_rtsp_port
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return host, port


def _credentials_for_target(
    source: CameraSource,
    camera: CameraRecord,
    target: RtspTarget,
) -> tuple[str, str]:
    if target == "edge":
        gw = camera.output.gateway
        user = gw.username or source.username
        pwd = gw.password.get_secret_value() or source.password.get_secret_value()
        return user, pwd
    return source.username, source.password.get_secret_value()


def build_camera_rtsp_url(
    camera: CameraRecord,
    *,
    mode: RtspMode = "stream",
    target: RtspTarget = "device",
    settings: AppSettings | None = None,
    starttime: datetime | None = None,
    endtime: datetime | None = None,
    brands_dir=None,
) -> str:
    source = camera.source
    brand = (source.brand or "").strip().lower()

    if brand:
        root = brands_dir or (
            default_brands_dir(settings.config_dir) if settings else default_brands_dir()
        )
        profile = load_brand_profile(brand, root)
        ok, err = profile_matches_model(profile, source.model or None)
        if not ok:
            raise ValueError(err)

        if target == "edge":
            if settings is None:
                raise ValueError("settings required for edge RTSP URLs")
            host, port = _edge_rtsp_host_port(settings)
        else:
            host, port = source.host, source.port

        user, pwd = _credentials_for_target(source, camera, target)
        values = build_rtsp_template_values(
            username=user,
            password=pwd,
            host=host,
            port=port,
            channel=source.channel or "101",
        )
        if mode == "playback" and (starttime is None or endtime is None):
            now = datetime.now(UTC)
            offset = source.time_offset_minutes
            endtime = endtime or now
            starttime = starttime or (now - timedelta(seconds=30))
        return render_rtsp_url(
            profile,
            mode=mode,
            values=values,
            starttime=starttime,
            endtime=endtime,
            time_offset_minutes=source.time_offset_minutes,
        )

    if mode == "playback":
        raise ValueError("playback RTSP requiere source.brand con plantilla del fabricante")

    if target == "edge" and settings:
        user, pwd = _credentials_for_target(source, camera, target)
        host, port = _edge_rtsp_host_port(settings)
        path = source.path or "/Streaming/Channels/101"
        if not path.startswith("/"):
            path = f"/{path}"
        auth = f"{user}:{pwd}@" if user else ""
        return f"rtsp://{auth}{host}:{port}{path}"

    return source.rtsp_url_legacy()


def default_gateway_path(camera: CameraRecord, settings: AppSettings | None = None) -> str:
    """Ruta pública RTSP en el edge (misma estructura que el fabricante si hay marca)."""
    gw_path = camera.output.gateway.path.strip("/")
    if gw_path:
        return gw_path
    brand = (camera.source.brand or "").strip().lower()
    if brand and settings:
        try:
            url = build_camera_rtsp_url(camera, mode="stream", target="edge", settings=settings)
            path = rtsp_path_from_url(url)
            if path:
                return path
        except (FileNotFoundError, ValueError):
            pass
    return camera.camera_id


def parse_legacy_path_from_url(url: str) -> str:
    return urlparse(url).path or "/Streaming/Channels/101"
