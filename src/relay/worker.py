"""Worker FFmpeg: RTSP passthrough listen (servidor) o push (salida)."""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from enum import Enum

from src.config_loader import AppSettings
from src.discovery.models import CameraRecord, CameraRelayOutput

logger = logging.getLogger(__name__)


class RelayMode(str, Enum):
    LISTEN = "listen"
    PUSH = "push"


def relay_mode(relay: CameraRelayOutput) -> RelayMode:
    raw = getattr(relay, "mode", RelayMode.LISTEN.value)
    try:
        return RelayMode(str(raw).lower())
    except ValueError:
        return RelayMode.LISTEN


def build_relay_urls(
    camera: CameraRecord,
    settings: AppSettings,
    listen_port: int | None = None,
) -> tuple[str, str]:
    """
    Devuelve (input_rtsp_url, output_rtsp_url).
    output usa modo listen (servidor en edge) o push (URL remota).
    """
    relay = camera.output.relay
    source_url = camera.rtsp_url()
    mode = relay_mode(relay)
    port = listen_port if listen_port is not None else relay.listen_port

    if mode == RelayMode.PUSH:
        if not relay.push_url:
            raise ValueError(f"relay.push_url requerido para modo push: {camera.camera_id}")
        return source_url, relay.push_url

    auth_user = relay.username
    auth_pass = relay.password.get_secret_value()
    cred = ""
    if auth_user:
        cred = f"{auth_user}:{auth_pass}@" if auth_pass else f"{auth_user}@"

    host = relay.listen_host or settings.edge_rtsp_host
    path = relay.path
    output_url = f"rtsp://{cred}{host}:{port}{path}"
    return source_url, output_url


def build_ffmpeg_command(
    camera: CameraRecord,
    settings: AppSettings,
    listen_port: int | None = None,
) -> list[str]:
    relay = camera.output.relay
    source_url, output_url = build_relay_urls(camera, settings, listen_port)
    transport = camera.source.transport or "tcp"
    mode = relay_mode(relay)

    cmd = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        settings.ffmpeg_log_level,
        "-rtsp_transport",
        transport,
        "-i",
        source_url,
        "-map",
        "0:v:0",
    ]

    if relay.force_transcode_gop:
        fps = max(1, camera.source.fps)
        gop = max(1, int(fps * relay.iframe_interval_sec))
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                settings.relay_transcode_preset,
                "-tune",
                "zerolatency",
                "-g",
                str(gop),
                "-keyint_min",
                str(gop),
                "-sc_threshold",
                "0",
            ]
        )
    else:
        cmd.extend(["-c:v", "copy"])

    cmd.extend(["-an", "-f", "rtsp", "-rtsp_transport", "tcp"])
    if mode == RelayMode.LISTEN:
        cmd.extend(["-rtsp_flags", "listen"])
    cmd.append(output_url)
    return cmd


def relay_config_signature(
    camera: CameraRecord,
    settings: AppSettings,
    listen_port: int | None = None,
) -> str:
    """Huella de configuración para detectar cambios y reiniciar relay."""
    relay = camera.output.relay
    cmd = build_ffmpeg_command(camera, settings, listen_port)
    return "|".join(
        [
            camera.camera_id,
            camera.rtsp_url(),
            relay.mode if hasattr(relay, "mode") else "listen",
            relay.push_url,
            str(listen_port or relay.listen_port),
            relay.path,
            str(relay.force_transcode_gop),
            str(relay.iframe_interval_sec),
            " ".join(cmd),
        ]
    )


class FfmpegRtspRelay:
    """Subproceso FFmpeg con reinicio exponencial."""

    def __init__(
        self,
        camera: CameraRecord,
        settings: AppSettings,
        listen_port: int | None = None,
    ) -> None:
        self._camera = camera
        self._settings = settings
        self._listen_port = listen_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._running = False
        self._restarts = 0
        self._last_error: str | None = None
        self._started_at: float | None = None
        self._config_sig = relay_config_signature(camera, settings, listen_port)

    @property
    def camera_id(self) -> str:
        return self._camera.camera_id

    @property
    def config_signature(self) -> str:
        return self._config_sig

    def update_camera(self, camera: CameraRecord, listen_port: int | None = None) -> bool:
        """Actualiza cámara; devuelve True si la config cambió (requiere reinicio)."""
        new_sig = relay_config_signature(camera, self._settings, listen_port)
        changed = new_sig != self._config_sig
        self._camera = camera
        self._listen_port = listen_port
        self._config_sig = new_sig
        return changed

    @property
    def is_running(self) -> bool:
        """True solo si el subproceso FFmpeg está vivo (no basta el hilo supervisor)."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def get_status(self) -> dict:
        source_url, output_url = build_relay_urls(
            self._camera, self._settings, self._listen_port
        )
        with self._lock:
            pid = self._process.pid if self._process and self._process.poll() is None else None
        return {
            "camera_id": self.camera_id,
            "running": self.is_running,
            "pid": pid,
            "restarts": self._restarts,
            "last_error": self._last_error,
            "mode": relay_mode(self._camera.output.relay).value,
            "source_url_masked": _mask_url(source_url),
            "output_url_masked": _mask_url(output_url),
            "force_transcode_gop": self._camera.output.relay.force_transcode_gop,
            "iframe_interval_sec": self._camera.output.relay.iframe_interval_sec,
            "uptime_sec": round(time.monotonic() - self._started_at, 2)
            if self._started_at
            else 0,
        }

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._supervisor,
            name=f"relay-{self._camera.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Relay RTSP iniciado: %s", self._camera.camera_id)

    def stop(self) -> None:
        self._stop.set()
        self._terminate_process()
        if self._thread:
            self._thread.join(timeout=15.0)
            self._thread = None
        logger.info("Relay RTSP detenido: %s", self._camera.camera_id)

    def restart(self) -> None:
        self._terminate_process()
        self._restarts += 1

    def _terminate_process(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            logger.debug("Error terminando ffmpeg %s", self.camera_id, exc_info=True)

    def _supervisor(self) -> None:
        delay = self._settings.reconnect_base_delay
        self._running = True
        while not self._stop.is_set():
            cmd = build_ffmpeg_command(self._camera, self._settings, self._listen_port)
            logger.info(
                "Relay %s: %s",
                self.camera_id,
                " ".join(shlex.quote(c) for c in cmd[:8]) + " ...",
            )
            try:
                with self._lock:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                self._started_at = time.monotonic()
                _, stderr = self._process.communicate()
                rc = self._process.returncode
                if self._stop.is_set():
                    break
                err_tail = (stderr or b"")[-500:].decode("utf-8", errors="replace")
                self._last_error = f"exit={rc} {err_tail}".strip()
                logger.warning("Relay %s terminó: %s", self.camera_id, self._last_error)
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Relay %s error", self.camera_id)
            finally:
                with self._lock:
                    self._process = None

            if self._stop.is_set():
                break
            self._restarts += 1
            self._stop.wait(delay)
            delay = min(delay * 2, self._settings.reconnect_max_delay)
        self._running = False

    def matches_signature(self, camera: CameraRecord, listen_port: int | None) -> bool:
        return (
            relay_config_signature(camera, self._settings, listen_port) == self._config_sig
        )


def _mask_url(url: str) -> str:
    if "@" not in url:
        return url
    prefix, rest = url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"{prefix}://***@{hostpart}"
