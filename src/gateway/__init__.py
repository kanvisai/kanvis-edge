"""RTSP Gateway unificado (MediaMTX): un puerto, rutas por cámara."""

from src.gateway.config import build_gateway_client_url, generate_mediamtx_config
from src.gateway.manager import GatewayManager

__all__ = [
    "GatewayManager",
    "build_gateway_client_url",
    "generate_mediamtx_config",
]
