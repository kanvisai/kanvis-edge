"""Captura de frame JPEG desde URL RTSP (origen o relay) vía FFmpeg."""

from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)


class SnapshotError(Exception):
    """Error al capturar snapshot."""


def _capture_ffmpeg_sync(
    ffmpeg_path: str,
    rtsp_url: str,
    transport: str,
    timeout_sec: float,
) -> bytes:
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        transport,
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-q:v",
        "2",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"Timeout ({timeout_sec}s) capturando frame") from exc
    except FileNotFoundError as exc:
        raise SnapshotError("ffmpeg no encontrado en PATH") from exc

    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise SnapshotError(f"ffmpeg falló: {err}")
    if not result.stdout:
        raise SnapshotError("ffmpeg no devolvió imagen")
    return result.stdout


async def capture_jpeg_from_rtsp(
    rtsp_url: str,
    ffmpeg_path: str = "ffmpeg",
    transport: str = "tcp",
    timeout_sec: float = 10.0,
) -> bytes:
    """Captura un frame JPEG desde RTSP."""
    return await asyncio.to_thread(
        _capture_ffmpeg_sync,
        ffmpeg_path,
        rtsp_url,
        transport,
        timeout_sec,
    )


def local_listen_url(public_url: str) -> str:
    """Convierte 0.0.0.0 en 127.0.0.1 para captura local del relay."""
    return public_url.replace("0.0.0.0", "127.0.0.1")
