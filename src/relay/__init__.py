"""Módulo relay: RTSP passthrough (FFmpeg codec copy / GOP opcional)."""

from src.relay.manager import RelayManager
from src.relay.worker import FfmpegRtspRelay, RelayMode, build_relay_urls

__all__ = ["RelayManager", "FfmpegRtspRelay", "RelayMode", "build_relay_urls"]
