"""Modelos de dominio: cámara con entrada RTSP, salida relay/WebRTC y búfer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SecretStr, model_validator


class OutputProtocol(str, Enum):
    NONE = "none"
    RTSP = "rtsp"
    WEBRTC = "webrtc"


class ExternalAccessMode(str, Enum):
    """
    Cómo acceder a la cámara desde internet (documentación + validación).

    - direct: port forwarding WAN → cámara:554 (sin proxy en el edge)
    - gateway: un puerto WAN → edge MediaMTX → path /{camera_id}
    - relay: FFmpeg listen por cámara (puerto distinto por cámara)
    """

    DIRECT = "direct"
    GATEWAY = "gateway"
    RELAY = "relay"


class CameraSource(BaseModel):
    """Entrada RTSP desde la cámara en LAN."""

    host: str = Field(description="IP o hostname de la cámara")
    port: int = 554
    username: str = ""
    password: SecretStr = Field(default=SecretStr(""))
    brand: str = Field(
        default="",
        description="Slug de config/brands/<slug>.json (p. ej. annke)",
    )
    model: str = Field(default="", description="Modelo concreto si el perfil lo restringe")
    channel: str = Field(
        default="101",
        description="Canal lógico RTSP del fabricante (Annke/Hik: 101 main, 102 sub)",
    )
    time_offset_minutes: float = Field(
        default=0.0,
        description="Desfase horario para playback del fabricante",
    )
    path: str = Field(
        default="",
        description="Ruta RTSP manual si brand está vacío; si no, se ignora y usa la plantilla",
    )
    transport: str = "tcp"
    fps: int = 20
    width: int = 1280
    height: int = 720

    def rtsp_url_legacy(self) -> str:
        user = self.username
        pwd = self.password.get_secret_value()
        auth = f"{user}:{pwd}@" if user else ""
        path = self.path or "/Streaming/Channels/101"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"rtsp://{auth}{self.host}:{self.port}{path}"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


class CameraGatewayOutput(BaseModel):
    """Proxy RTSP unificado (MediaMTX) — un puerto, ruta por cámara."""

    enabled: bool = False
    access_mode: ExternalAccessMode = ExternalAccessMode.GATEWAY
    path: str = ""
    username: str = ""
    password: SecretStr = Field(default=SecretStr(""))
    source_on_demand: bool = True
    source_on_demand_close_after: float = 10.0


class CameraRelayOutput(BaseModel):
    """Salida RTSP rebroadcast (passthrough FFmpeg)."""

    enabled: bool = False
    mode: str = "listen"  # listen | push
    push_url: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = 8554
    path_suffix: str = ""
    username: str = ""
    password: SecretStr = Field(default=SecretStr(""))
    iframe_interval_sec: float = 3.0
    force_transcode_gop: bool = False

    @property
    def path(self) -> str:
        suffix = self.path_suffix.strip("/")
        return f"/{suffix}" if suffix else "/live"


class CameraWebRTCOutput(BaseModel):
    """Salida WebRTC (WHEP local o WHIP hacia nube)."""

    enabled: bool = False
    mode: str = "whep"  # whep = visor en navegador; whip = push a signaling_url
    signaling_url: str = ""
    room_id: str = ""
    stun_urls: list[str] = Field(default_factory=lambda: ["stun:stun.l.google.com:19302"])
    rewind_offset_sec: float = 3.0
    auto_connect_whip: bool = False


class CameraOutput(BaseModel):
    protocol: OutputProtocol = OutputProtocol.NONE
    gateway: CameraGatewayOutput = Field(default_factory=CameraGatewayOutput)
    relay: CameraRelayOutput = Field(default_factory=CameraRelayOutput)
    webrtc: CameraWebRTCOutput = Field(default_factory=CameraWebRTCOutput)


class CameraBufferSettings(BaseModel):
    """Búfer en RAM para playback (segundos de ventana hacia atrás)."""

    duration_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    default_playback_offset_sec: float = Field(default=6.0, ge=0.1, le=120.0)
    event_pre_seconds: float = Field(default=6.0, ge=0.1, le=120.0)
    event_post_seconds: float = Field(default=24.0, ge=0.0, le=120.0)


class CameraRecord(BaseModel):
    """Entidad de cámara persistida en el inventario local."""

    camera_id: str
    enabled: bool = True
    label: str = ""
    source: CameraSource
    output: CameraOutput = Field(default_factory=CameraOutput)
    buffer: CameraBufferSettings = Field(default_factory=CameraBufferSettings)

    # --- Compatibilidad API legacy (propiedades planas) ---

    @property
    def ip_address(self) -> str:
        return self.source.host

    @property
    def rtsp_port(self) -> int:
        return self.source.port

    @property
    def username(self) -> str:
        return self.source.username

    @property
    def password(self) -> SecretStr:
        return self.source.password

    @property
    def rtsp_path(self) -> str:
        return self.source.path

    @property
    def fps(self) -> int:
        return self.source.fps

    @property
    def resolution(self) -> str:
        return self.source.resolution

    def rtsp_url(self, stream_path: str | None = None, settings: Any | None = None) -> str:
        from src.discovery.rtsp_urls import build_camera_rtsp_url

        if stream_path:
            src = self.source.model_copy(update={"path": stream_path, "brand": ""})
            cam = self.model_copy(update={"source": src})
            return cam.source.rtsp_url_legacy()
        return build_camera_rtsp_url(
            self, mode="stream", target="device", settings=settings
        )

    def rtsp_playback_url(
        self,
        *,
        starttime: Any,
        endtime: Any,
        settings: Any | None = None,
        target: str = "device",
    ) -> str:
        from src.discovery.rtsp_urls import RtspTarget, build_camera_rtsp_url

        return build_camera_rtsp_url(
            self,
            mode="playback",
            target=target,  # type: ignore[arg-type]
            settings=settings,
            starttime=starttime,
            endtime=endtime,
        )

    def rtsp_urls_summary(self, settings: Any) -> dict[str, str]:
        from src.discovery.rtsp_urls import build_camera_rtsp_url

        out: dict[str, str] = {
            "device_stream": build_camera_rtsp_url(
                self, mode="stream", target="device", settings=settings
            ),
        }
        if (self.source.brand or "").strip():
            out["edge_stream"] = build_camera_rtsp_url(
                self, mode="stream", target="edge", settings=settings
            )
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            out["device_playback"] = build_camera_rtsp_url(
                self,
                mode="playback",
                target="device",
                settings=settings,
                starttime=now - timedelta(seconds=30),
                endtime=now,
            )
            out["edge_playback"] = build_camera_rtsp_url(
                self,
                mode="playback",
                target="edge",
                settings=settings,
                starttime=now - timedelta(seconds=30),
                endtime=now,
            )
        return out

    def effective_buffer_duration(self, global_default: float) -> float:
        return self.buffer.duration_seconds or global_default

    def model_dump_for_storage(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data["source"]["password"] = self.source.password.get_secret_value()
        data["output"]["relay"]["password"] = self.output.relay.password.get_secret_value()
        data["output"]["gateway"]["password"] = self.output.gateway.password.get_secret_value()
        return data

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> CameraRecord:
        normalized = _normalize_legacy_camera_dict(data)
        src = normalized["source"]
        out = normalized.get("output", {})
        gateway = out.get("gateway", {})
        relay = out.get("relay", {})
        webrtc = out.get("webrtc", {})
        buf = normalized.get("buffer", {})

        return cls(
            camera_id=normalized["camera_id"],
            enabled=normalized.get("enabled", True),
            label=normalized.get("label", ""),
            source=CameraSource(
                host=src["host"],
                port=src.get("port", 554),
                username=src.get("username", ""),
                password=SecretStr(src.get("password", "")),
                brand=src.get("brand", ""),
                model=src.get("model", ""),
                channel=str(src.get("channel", "101")),
                time_offset_minutes=float(src.get("time_offset_minutes", 0.0)),
                path=src.get("path", ""),
                transport=src.get("transport", "tcp"),
                fps=src.get("fps", 20),
                width=src.get("width", 1280),
                height=src.get("height", 720),
            ),
            output=CameraOutput(
                protocol=OutputProtocol(out.get("protocol", OutputProtocol.NONE)),
                gateway=CameraGatewayOutput(
                    enabled=gateway.get("enabled", False),
                    access_mode=_parse_external_access_mode(
                        gateway.get("access_mode", ExternalAccessMode.GATEWAY)
                    ),
                    path=gateway.get("path", ""),
                    username=gateway.get("username", ""),
                    password=SecretStr(gateway.get("password", "")),
                    source_on_demand=bool(gateway.get("source_on_demand", True)),
                    source_on_demand_close_after=float(
                        gateway.get("source_on_demand_close_after", 10.0)
                    ),
                ),
                relay=CameraRelayOutput(
                    enabled=relay.get("enabled", False),
                    mode=relay.get("mode", "listen"),
                    push_url=relay.get("push_url", ""),
                    listen_host=relay.get("listen_host", "0.0.0.0"),
                    listen_port=relay.get("listen_port", 8554),
                    path_suffix=relay.get("path_suffix", ""),
                    username=relay.get("username", ""),
                    password=SecretStr(relay.get("password", "")),
                    iframe_interval_sec=float(relay.get("iframe_interval_sec", 3.0)),
                    force_transcode_gop=bool(relay.get("force_transcode_gop", False)),
                ),
                webrtc=CameraWebRTCOutput(
                    enabled=webrtc.get("enabled", False),
                    mode=webrtc.get("mode", "whep"),
                    signaling_url=webrtc.get("signaling_url", ""),
                    room_id=webrtc.get("room_id", ""),
                    stun_urls=webrtc.get(
                        "stun_urls", ["stun:stun.l.google.com:19302"]
                    ),
                    rewind_offset_sec=float(webrtc.get("rewind_offset_sec", 3.0)),
                    auto_connect_whip=bool(webrtc.get("auto_connect_whip", False)),
                ),
            ),
            buffer=CameraBufferSettings(
                duration_seconds=float(buf.get("duration_seconds", 60.0)),
                default_playback_offset_sec=float(
                    buf.get("default_playback_offset_sec", 6.0)
                ),
                event_pre_seconds=float(buf.get("event_pre_seconds", 6.0)),
                event_post_seconds=float(buf.get("event_post_seconds", 24.0)),
            ),
        )


def _parse_external_access_mode(value: Any) -> ExternalAccessMode:
    if isinstance(value, ExternalAccessMode):
        return value
    try:
        return ExternalAccessMode(str(value))
    except ValueError:
        return ExternalAccessMode.GATEWAY


def _normalize_legacy_camera_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Convierte formato plano legacy a esquema anidado."""
    if "source" in data:
        return data

    res = data.get("resolution", "1280x720")
    width, height = 1280, 720
    if "x" in str(res):
        parts = str(res).lower().split("x", 1)
        try:
            width, height = int(parts[0]), int(parts[1])
        except ValueError:
            pass

    return {
        "camera_id": data["camera_id"],
        "enabled": data.get("enabled", True),
        "label": data.get("label", ""),
        "source": {
            "host": data.get("ip_address", data.get("host", "")),
            "port": data.get("rtsp_port", 554),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "path": data.get("rtsp_path", "/Streaming/Channels/101"),
            "fps": data.get("fps", 20),
            "width": width,
            "height": height,
        },
        "output": data.get(
            "output",
            {
                "protocol": "none",
                "relay": {"enabled": False},
                "webrtc": {"enabled": False},
            },
        ),
        "buffer": data.get(
            "buffer",
            {
                "duration_seconds": 60.0,
                "default_playback_offset_sec": 6.0,
                "event_pre_seconds": 6.0,
                "event_post_seconds": 24.0,
            },
        ),
    }


class CameraCreatePayload(BaseModel):
    """Payload API para crear cámara (plano o anidado)."""

    camera_id: str
    enabled: bool = True
    label: str = ""
    source: CameraSource | None = None
    output: CameraOutput | None = None
    buffer: CameraBufferSettings | None = None
    ip_address: str | None = None
    rtsp_port: int | None = None
    username: str | None = None
    password: str | None = None
    rtsp_path: str | None = None
    fps: int | None = None
    resolution: str | None = None

    @model_validator(mode="after")
    def build_source(self) -> CameraCreatePayload:
        if self.source is not None:
            return self
        if not self.ip_address:
            raise ValueError("Se requiere 'source' o 'ip_address'")
        width, height = 1280, 720
        if self.resolution and "x" in self.resolution:
            w, h = self.resolution.lower().split("x", 1)
            width, height = int(w), int(h)
        object.__setattr__(
            self,
            "source",
            CameraSource(
                host=self.ip_address,
                port=self.rtsp_port or 554,
                username=self.username or "",
                password=SecretStr(self.password or ""),
                brand="",
                path=self.rtsp_path or "",
                fps=self.fps or 20,
                width=width,
                height=height,
            ),
        )
        return self

    def to_record(self) -> CameraRecord:
        return CameraRecord(
            camera_id=self.camera_id,
            enabled=self.enabled,
            label=self.label,
            source=self.source or CameraSource(host="0.0.0.0"),
            output=self.output or CameraOutput(),
            buffer=self.buffer or CameraBufferSettings(),
        )
