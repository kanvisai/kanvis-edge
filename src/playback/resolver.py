"""Resolución de cámara y perfil desde ruta RTSP del gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.brands import load_brand_profile
from src.brands.registry import default_brands_dir
from src.discovery.models import CameraRecord, ExternalAccessMode
from src.discovery.rtsp_urls import gateway_playback_path, gateway_stream_path
from src.playback.parse import normalize_gateway_path

if TYPE_CHECKING:
    from src.config_loader import AppSettings


def find_camera_for_gateway_path(
    path: str,
    cameras: list[CameraRecord],
    settings: AppSettings,
) -> CameraRecord | None:
    """Busca cámara cuyo path de playback o stream coincide con MTX_PATH."""
    norm = normalize_gateway_path(path)
    if not norm:
        return None
    for camera in cameras:
        if not camera.enabled or not camera.output.gateway.enabled:
            continue
        if camera.output.gateway.access_mode == ExternalAccessMode.DIRECT:
            continue
        for getter in (gateway_playback_path, gateway_stream_path):
            try:
                gw_path = getter(camera, settings)
            except (FileNotFoundError, ValueError):
                continue
            if normalize_gateway_path(gw_path) == norm:
                return camera
        gw = camera.output.gateway.path.strip("/")
        if gw and gw == norm:
            return camera
        if camera.camera_id == norm:
            return camera
    return None


def brand_profile_for_camera(camera: CameraRecord, settings: AppSettings):
    brand = (camera.source.brand or "").strip().lower()
    if not brand:
        return None
    return load_brand_profile(brand, default_brands_dir(settings.config_dir))
