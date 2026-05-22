"""Generación de configuración MediaMTX desde el inventario de cámaras."""

from __future__ import annotations

from typing import Any

import yaml

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, ExternalAccessMode
from src.discovery.rtsp_urls import default_gateway_path


def _gateway_path(camera: CameraRecord, settings: AppSettings | None = None) -> str:
    if settings is not None:
        return default_gateway_path(camera, settings)
    raw = camera.output.gateway.path.strip("/")
    return raw or camera.camera_id


def cameras_for_gateway(cameras: list[CameraRecord]) -> list[CameraRecord]:
    """Cámaras que deben publicarse en el gateway unificado."""
    return [
        c
        for c in cameras
        if c.enabled
        and c.output.gateway.enabled
        and c.output.gateway.access_mode != ExternalAccessMode.DIRECT
    ]


def generate_mediamtx_config(
    cameras: list[CameraRecord],
    settings: AppSettings,
) -> dict[str, Any]:
    """
    Config MediaMTX: un listener RTSP y un path por cámara (pull on-demand).

    Las cámaras con external_access=direct no entran aquí (PF directo a la cámara).
    """
    host = settings.rtsp_gateway_listen_host
    port = settings.rtsp_gateway_port
    paths: dict[str, Any] = {}

    for camera in cameras_for_gateway(cameras):
        gw = camera.output.gateway
        path_name = _gateway_path(camera, settings)
        entry: dict[str, Any] = {
            "source": camera.rtsp_url(settings=settings),
            "sourceProtocol": camera.source.transport or "tcp",
            "sourceOnDemand": gw.source_on_demand,
            "sourceOnDemandStartTimeout": "10s",
            "sourceOnDemandCloseAfter": f"{int(gw.source_on_demand_close_after)}s",
        }
        read_user = gw.username or ""
        read_pass = gw.password.get_secret_value()
        if read_user:
            entry["readUser"] = read_user
        if read_pass:
            entry["readPass"] = read_pass
        paths[path_name] = entry

    return {
        "logLevel": settings.mediamtx_log_level,
        "rtsp": True,
        "rtspAddress": f"{host}:{port}",
        "rtspTransports": ["tcp"],
        "paths": paths,
    }


def render_mediamtx_yaml(config: dict[str, Any]) -> str:
    return yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)


def gateway_config_signature(
    cameras: list[CameraRecord],
    settings: AppSettings,
) -> str:
    """Huella para decidir si hay que reiniciar MediaMTX."""
    parts: list[str] = [
        str(settings.rtsp_gateway_enabled),
        settings.rtsp_gateway_listen_host,
        str(settings.rtsp_gateway_port),
        settings.mediamtx_log_level,
    ]
    for cam in sorted(cameras_for_gateway(cameras), key=lambda c: c.camera_id):
        gw = cam.output.gateway
        parts.extend(
            [
                cam.camera_id,
                cam.rtsp_url(settings=settings),
                _gateway_path(cam, settings),
                str(gw.source_on_demand),
                str(int(gw.source_on_demand_close_after)),
                gw.username,
                gw.password.get_secret_value(),
            ]
        )
    return "|".join(parts)


def build_gateway_client_url(
    camera: CameraRecord,
    settings: AppSettings,
    *,
    public_host: str | None = None,
) -> str:
    """URL RTSP para clientes externos (misma estructura que el fabricante si hay marca)."""
    from src.discovery.rtsp_urls import build_camera_rtsp_url

    if (camera.source.brand or "").strip():
        host = public_host
        if host:
            port = (
                settings.rtsp_gateway_port
                if settings.rtsp_gateway_enabled
                else settings.edge_rtsp_port
            )
            gw = camera.output.gateway
            user = gw.username or camera.source.username
            pwd = gw.password.get_secret_value() or camera.source.password.get_secret_value()
            from src.brands import build_rtsp_template_values, load_brand_profile, render_rtsp_url

            from src.brands.registry import default_brands_dir

            profile = load_brand_profile(
                camera.source.brand,
                default_brands_dir(settings.config_dir),
            )
            values = build_rtsp_template_values(
                username=user,
                password=pwd,
                host=host,
                port=port,
                channel=camera.source.channel or "101",
            )
            return render_rtsp_url(profile, mode="stream", values=values)
        return build_camera_rtsp_url(
            camera, mode="stream", target="edge", settings=settings
        )

    gw = camera.output.gateway
    host = public_host or settings.rtsp_gateway_listen_host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = settings.rtsp_gateway_port
    path_name = _gateway_path(camera, settings)
    user = gw.username
    pwd = gw.password.get_secret_value()
    auth = f"{user}:{pwd}@" if user else (f":{pwd}@" if pwd else "")
    return f"rtsp://{auth}{host}:{port}/{path_name}"
