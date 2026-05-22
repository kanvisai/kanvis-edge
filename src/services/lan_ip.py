"""IP LAN del edge (para enlaces del panel en la red local)."""

from __future__ import annotations

import socket


def detect_edge_lan_ip(fallback: str = "") -> str:
    """
    IP usada para salir a internet (ruta por defecto).
    No es la IP pública; sirve para abrir el panel desde la misma LAN.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.8)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    fb = (fallback or "").strip()
    if fb and not fb.startswith("127."):
        return fb
    return ""
