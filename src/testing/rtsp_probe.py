"""Análisis RTSP (códec) para «Probar conexión»."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass

from src.testing.snapshot import SnapshotError


@dataclass(frozen=True)
class RtspStreamProbe:
    codec_name: str
    codec_long_name: str = ""
    width: int | None = None
    height: int | None = None
    recommendation: str = "rtsp"
    recommendation_label: str = ""

    def as_dict(self) -> dict:
        return {
            "codec_name": self.codec_name,
            "codec_long_name": self.codec_long_name,
            "width": self.width,
            "height": self.height,
            "resolution": (
                f"{self.width}×{self.height}"
                if self.width and self.height
                else None
            ),
            "recommendation": self.recommendation,
            "recommendation_label": self.recommendation_label,
        }


def ffprobe_path_from_ffmpeg(ffmpeg_path: str) -> str:
    p = (ffmpeg_path or "ffmpeg").strip()
    if p.endswith("ffmpeg"):
        return p[: -len("ffmpeg")] + "ffprobe"
    return "ffprobe"


def broadcast_recommendation(codec_name: str) -> tuple[str, str]:
    """Devuelve (modo sugerido: webrtc|rtsp, texto para el usuario)."""
    c = (codec_name or "").strip().lower()
    if c in ("h264", "avc", "avc1", "mpeg4", "mp4v"):
        return (
            "webrtc",
            "H.264 — recomendado broadcast WebRTC en el panel.",
        )
    if c in ("hevc", "h265", "hev1", "hvc1"):
        return (
            "rtsp",
            "H.265/HEVC — usa broadcast RTSP (relay); reenvía el stream tal cual.",
        )
    if not c:
        return ("rtsp", "Códec no detectado — prueba RTSP relay.")
    return (
        "rtsp",
        f"Códec «{codec_name}» — prueba RTSP relay; WebRTC solo fiable con H.264.",
    )


def _probe_sync(
    url: str,
    ffprobe_path: str,
    transport: str,
    timeout_sec: float,
) -> RtspStreamProbe:
    cmd = [
        ffprobe_path,
        "-hide_banner",
        "-v",
        "error",
        "-rtsp_transport",
        transport or "tcp",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,codec_long_name,width,height",
        "-of",
        "json",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"Timeout ({timeout_sec}s) analizando RTSP") from exc
    except FileNotFoundError as exc:
        raise SnapshotError("ffprobe no encontrado (instala ffmpeg)") from exc

    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise SnapshotError(f"ffprobe falló: {err}")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SnapshotError("ffprobe devolvió JSON inválido") from exc

    streams = data.get("streams") or []
    if not streams:
        raise SnapshotError("No se encontró pista de vídeo en el RTSP")

    st = streams[0]
    codec = str(st.get("codec_name") or "").strip()
    mode, label = broadcast_recommendation(codec)
    width = st.get("width")
    height = st.get("height")
    return RtspStreamProbe(
        codec_name=codec,
        codec_long_name=str(st.get("codec_long_name") or ""),
        width=int(width) if width else None,
        height=int(height) if height else None,
        recommendation=mode,
        recommendation_label=label,
    )


def _probe_pyav_sync(url: str, transport: str, timeout_sec: float) -> RtspStreamProbe:
    import av

    container = None
    try:
        usec = max(1, int(timeout_sec * 1_000_000))
        container = av.open(
            url,
            options={
                "rtsp_transport": transport or "tcp",
                "stimeout": str(usec),
            },
        )
        videos = container.streams.video
        if not videos:
            raise SnapshotError("PyAV: sin pista de vídeo en el RTSP")
        stream = videos[0]
        codec = (getattr(stream.codec, "name", None) or "").strip()
        if not codec:
            raise SnapshotError("PyAV no obtuvo códec de vídeo")
        mode, label = broadcast_recommendation(codec)
        ctx = getattr(stream, "codec_context", None)
        w = getattr(ctx, "width", None) if ctx else None
        h = getattr(ctx, "height", None) if ctx else None
        if not w:
            w = getattr(stream, "width", None)
        if not h:
            h = getattr(stream, "height", None)
        return RtspStreamProbe(
            codec_name=codec,
            codec_long_name=codec,
            width=int(w) if w else None,
            height=int(h) if h else None,
            recommendation=mode,
            recommendation_label=label + " (detectado con PyAV)",
        )
    except av.AVError as exc:
        raise SnapshotError(f"PyAV: {exc}") from exc
    except IndexError as exc:
        raise SnapshotError("PyAV: sin stream de vídeo") from exc
    except Exception as exc:
        raise SnapshotError(f"PyAV: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                pass


async def probe_rtsp_stream(
    url: str,
    ffmpeg_path: str = "ffmpeg",
    transport: str = "tcp",
    timeout_sec: float = 10.0,
) -> RtspStreamProbe:
    ffprobe = ffprobe_path_from_ffmpeg(ffmpeg_path)
    try:
        return await asyncio.to_thread(
            _probe_sync, url, ffprobe, transport, timeout_sec
        )
    except SnapshotError:
        try:
            return await asyncio.to_thread(
                _probe_pyav_sync, url, transport, min(timeout_sec, 8.0)
            )
        except SnapshotError:
            raise
        except Exception as exc:
            raise SnapshotError(str(exc)) from exc
