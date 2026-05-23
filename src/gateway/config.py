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
        f"{ff} -loglevel warning -nostdin -re -f h264 "
        f"-i http://127.0.0.1:{api_port}/api/v1/internal/rtsp-playback"
        "?mtx_path=$MTX_PATH&mtx_query=$MTX_QUERY "
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
    """
    from datetime import datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlparse

    from src.brands import default_brands_dir, load_brand_profile
    from src.discovery.rtsp_urls import (
        build_camera_rtsp_url,
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
        stream_tpl = build_camera_rtsp_url(
            camera, mode="stream", target="edge", settings=settings
        )
        playback_tpl = build_camera_rtsp_url(
            camera,
            mode="playback",
            target="edge",
            settings=settings,
            starttime=start,
            endtime=end,
        )
        stream_path = gateway_stream_path(camera, settings)
        playback_path = gateway_playback_path(camera, settings)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"error": str(exc)}

    stream_lan = replace_rtsp_host_port(stream_tpl, lan, lan_port)
    playback_lan = replace_rtsp_host_port(playback_tpl, lan, lan_port)
    pub = (public_host or "").strip()
    stream_wan = playback_wan = ""
    if pub:
        stream_wan = replace_rtsp_host_port(stream_tpl, pub, wan_port)
        playback_wan = replace_rtsp_host_port(playback_tpl, pub, wan_port)

    mpv_tpl = 'mpv --rtsp-transport=tcp --no-audio "{url}"'
    pb_qs = parse_qs(urlparse(playback_lan).query)
    ex_start = (pb_qs.get("starttime") or [""])[0]
    ex_end = (pb_qs.get("endtime") or [""])[0]
    brand_slug = (camera.source.brand or "").strip().lower()
    requires_utc = True
    if brand_slug:
        try:
            profile = load_brand_profile(brand_slug, default_brands_dir(settings.config_dir))
            requires_utc = profile.protocols.rtsp.requires_utc
        except FileNotFoundError:
            pass
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
            "example_starttime": ex_start,
            "example_endtime": ex_end,
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
