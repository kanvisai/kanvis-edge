"""Valores para plantillas RTSP (placeholders {{user}}, {{host}}, …)."""

from __future__ import annotations

from urllib.parse import quote


def build_rtsp_template_values(
    *,
    username: str,
    password: str,
    host: str,
    port: int,
    channel: str,
) -> dict[str, str]:
    rtsp_port = int(port) if int(port) > 0 else 554
    return {
        "user": quote((username or "").strip(), safe=""),
        "password": quote((password or "").strip(), safe=""),
        "host": (host or "").strip(),
        "port": str(rtsp_port),
        "channel": str(channel).strip(),
    }
