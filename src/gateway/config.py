"""Generación de configuración MediaMTX desde el inventario de cámaras."""

from __future__ import annotations

from typing import Any

import yaml

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, ExternalAccessMode
from src.discovery.rtsp_urls import (
    default_gateway_path,
    gateway_playback_path,
    gateway_stream_path,
)


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


def build_playback_run_on_demand(settings: AppSettings) -> str:
    """FFmpeg publica en MediaMTX leyendo H.264 desde el API interno (búfer + vivo)."""
    api_port = settings.edge_api_port
    ff = settings.ffmpeg_path
    return (
        f"{ff} -loglevel warning -nostdin "
        f"-fflags +nobuffer+fastseek -flags low_delay "
        f"-probesize 4096 -analyzeduration 0 "
        f"-f h264 "
        f'-i "http://127.0.0.1:{api_port}/api/v1/internal/rtsp-playback'
        f'?mtx_path=$MTX_PATH&$MTX_QUERY" '
        f"-c copy -an -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    )


def _read_auth_entry(gw) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    read_user = gw.username or ""
    read_pass = gw.password.get_secret_value()
    if read_user:
        entry["readUser"] = read_user
    if read_pass:
        entry["readPass"] = read_pass
    return entry


def generate_mediamtx_config(
    cameras: list[CameraRecord],
    settings: AppSettings,
) -> dict[str, Any]:
    """
    Config MediaMTX: vivo (pull cámara) y playback (runOnDemand → búfer edge).

    Las cámaras con external_access=direct no entran aquí (PF directo a la cámara).
    """
    host = settings.rtsp_gateway_listen_host
    port = settings.rtsp_gateway_port
    paths: dict[str, Any] = {}
    playback_cmd = build_playback_run_on_demand(settings)

    for camera in cameras_for_gateway(cameras):
        gw = camera.output.gateway
        brand = (camera.source.brand or "").strip()

        if brand:
            try:
                stream_path = gateway_stream_path(camera, settings)
                paths[stream_path] = {
                    "source": camera.rtsp_url(settings=settings),
                    "sourceProtocol": camera.source.transport or "tcp",
                    "sourceOnDemand": gw.source_on_demand,
                    "sourceOnDemandStartTimeout": "10s",
                    "sourceOnDemandCloseAfter": f"{int(gw.source_on_demand_close_after)}s",
                    **_read_auth_entry(gw),
                }
                playback_path = gateway_playback_path(camera, settings)
                paths[playback_path] = {
                    "runOnDemand": playback_cmd,
                    "runOnDemandRestart": False,
                    "runOnDemandStartTimeout": "20s",
                    "runOnDemandCloseAfter": f"{int(gw.source_on_demand_close_after)}s",
                    **_read_auth_entry(gw),
                }
            except (FileNotFoundError, ValueError):
                brand = ""

        if not brand:
            path_name = _gateway_path(camera, settings)
            paths[path_name] = {
                "source": camera.rtsp_url(settings=settings),
                "sourceProtocol": camera.source.transport or "tcp",
                "sourceOnDemand": gw.source_on_demand,
                "sourceOnDemandStartTimeout": "10s",
                "sourceOnDemandCloseAfter": f"{int(gw.source_on_demand_close_after)}s",
                **_read_auth_entry(gw),
            }

    return {
        "logLevel": settings.mediamtx_log_level,
        "rtsp": True,
        "rtspAddress": f"{host}:{port}",
        "rtspTransports": ["tcp"],
        "paths": paths,
    }


def render_mediamtx_yaml(config: dict[str, Any]) -> str:
    # Una sola línea por runOnDemand (PyYAML/Go parten comandos largos si width es bajo).
    return yaml.dump(
        config,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=4096,
    )


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
    parts.append(build_playback_run_on_demand(settings))
    for cam in sorted(cameras_for_gateway(cameras), key=lambda c: c.camera_id):
        gw = cam.output.gateway
        brand = (cam.source.brand or "").strip()
        paths_sig = [_gateway_path(cam, settings), cam.rtsp_url(settings=settings)]
        if brand:
            try:
                paths_sig.extend(
                    [
                        gateway_stream_path(cam, settings),
                        gateway_playback_path(cam, settings),
                    ]
                )
            except (FileNotFoundError, ValueError):
                pass
        parts.extend(
            [
                cam.camera_id,
                *paths_sig,
                str(gw.source_on_demand),
                str(int(gw.source_on_demand_close_after)),
                gw.username,
                gw.password.get_secret_value(),
            ]
        )
    return "|".join(parts)


def replace_rtsp_host_port(url: str, host: str, port: int) -> str:
    """Sustituye host/puerto manteniendo credenciales, path y query."""
    from urllib.parse import quote, unquote, urlparse, urlunparse

    p = urlparse(url)
    user = unquote(p.username) if p.username else ""
    pwd = unquote(p.password) if p.password else ""
    auth = ""
    if user:
        auth = (
            f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
            if pwd
            else f"{quote(user, safe='')}@"
        )
    netloc = f"{auth}{host.strip()}:{int(port)}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


def build_gateway_access_urls(
    camera: CameraRecord,
    settings: AppSettings,
    *,
    lan_host: str,
    public_host: str = "",
) -> dict[str, Any]:
    """
    URLs RTSP gateway (vivo + playback) para LAN y WAN.

    El playback incluye starttime/endtime de ejemplo; el cliente debe recalcularlos.
    Paths prefijados con camera_id para unicidad entre múltiples cámaras.
    """
    from datetime import datetime, timedelta, timezone
    from urllib.parse import quote, urlencode, urlunparse

    from src.brands import default_brands_dir, load_brand_profile
    from src.discovery.rtsp_urls import (
        gateway_playback_path,
        gateway_stream_path,
    )

    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=6)
    end = now + timedelta(seconds=30)
    lan_port = settings.rtsp_gateway_port
    wan_port = settings.rtsp_gateway_wan_port
    lan = (lan_host or "").strip()
    if not lan:
        return {"error": "Sin IP LAN del edge"}

    try:
        stream_path = gateway_stream_path(camera, settings)
        playback_path = gateway_playback_path(camera, settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"error": str(exc)}

    gw = camera.output.gateway
    user = gw.username or camera.source.username
    pwd = gw.password.get_secret_value() or camera.source.password.get_secret_value()
    auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@" if user else ""

    def _build_url(host: str, port: int, path: str, query: str = "") -> str:
        netloc = f"{auth}{host}:{port}"
        return urlunparse(("rtsp", netloc, f"/{path}", "", query, ""))

    brand_slug = (camera.source.brand or "").strip().lower()
    requires_utc = True
    if brand_slug:
        try:
            profile = load_brand_profile(brand_slug, default_brands_dir(settings.config_dir))
            requires_utc = profile.protocols.rtsp.requires_utc
        except FileNotFoundError:
            pass

    if requires_utc:
        starttime_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        endtime_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        starttime_str = start.strftime("%Y-%m-%dT%H:%M:%S")
        endtime_str = end.strftime("%Y-%m-%dT%H:%M:%S")

    pb_query = urlencode({"starttime": starttime_str, "endtime": endtime_str})

    stream_lan = _build_url(lan, lan_port, stream_path)
    playback_lan = _build_url(lan, lan_port, playback_path, pb_query)
    pub = (public_host or "").strip()
    stream_wan = _build_url(pub, wan_port, stream_path) if pub else ""
    playback_wan = _build_url(pub, wan_port, playback_path, pb_query) if pub else ""

    mpv_tpl = 'mpv --rtsp-transport=tcp --no-audio "{url}"'
    time_kind = "UTC" if requires_utc else "hora local del edge"
    time_note = (
        f"Ventana de ejemplo (−6 s / +30 s, {time_kind}). "
        "Genera starttime/endtime nuevos en cada petición."
    )
    return {
        "lan_port": lan_port,
        "wan_port": wan_port,
        "stream_path": stream_path,
        "playback_path": playback_path,
        "stream": {
            "url_lan": stream_lan,
            "url_wan": stream_wan or None,
            "mpv_lan": mpv_tpl.format(url=stream_lan),
            "mpv_wan": mpv_tpl.format(url=stream_wan) if stream_wan else None,
        },
        "playback": {
            "url_lan": playback_lan,
            "url_wan": playback_wan or None,
            "mpv_lan": mpv_tpl.format(url=playback_lan),
            "mpv_wan": mpv_tpl.format(url=playback_wan) if playback_wan else None,
            "example_starttime": starttime_str,
            "example_endtime": endtime_str,
            "note": (
                f"{time_note} El edge sirve el tramo reciente desde el búfer RAM."
            ),
        },
    }


def build_gateway_client_url(
    camera: CameraRecord,
    settings: AppSettings,
    *,
    public_host: str | None = None,
) -> str:
    """URL RTSP para clientes externos (prefijada con camera_id para unicidad)."""
    from src.discovery.rtsp_urls import gateway_stream_path

    gw = camera.output.gateway
    host = public_host or settings.rtsp_gateway_listen_host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = settings.rtsp_gateway_port
    user = gw.username or camera.source.username
    pwd = gw.password.get_secret_value() or camera.source.password.get_secret_value()
    auth = f"{user}:{pwd}@" if user else (f":{pwd}@" if pwd else "")

    brand = (camera.source.brand or "").strip()
    if brand:
        try:
            path_name = gateway_stream_path(camera, settings)
        except (FileNotFoundError, ValueError):
            path_name = _gateway_path(camera, settings)
    else:
        path_name = _gateway_path(camera, settings)

    return f"rtsp://{auth}{host}:{port}/{path_name}"
