"""Carga de configuración externalizada (Twelve-Factor) con Pydantic Settings."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CameraStoreBackend(str, Enum):
    JSON = "json"
    SQLITE = "sqlite"


class DDNSProvider(str, Enum):
    DUCKDNS = "duckdns"
    NOIP = "noip"
    CUSTOM = "custom"


class AuthMode(str, Enum):
    API_KEY = "api_key"
    JWT = "jwt"


class AppSettings(BaseSettings):
    """Configuración global inmutable en tiempo de ejecución."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    # API Gateway
    edge_api_host: str = Field(default="0.0.0.0", alias="EDGE_API_HOST")
    edge_api_port: int = Field(default=8000, alias="EDGE_API_PORT")
    edge_panel_public_url: str = Field(
        default="",
        alias="EDGE_PANEL_PUBLIC_URL",
        description=(
            "URL pública del panel (ej. http://203.0.113.5:8000) para enlaces WebRTC/RTSP "
            "en la UI; si vacío se infiere del Host o de la IP WAN"
        ),
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # RTSP relay global (puerto por defecto si la cámara no define uno)
    edge_rtsp_port: int = Field(default=8554, alias="EDGE_RTSP_PORT")
    edge_rtsp_host: str = Field(default="0.0.0.0", alias="EDGE_RTSP_HOST")

    # RTSP gateway unificado MediaMTX (Fase 7, opcional)
    rtsp_gateway_enabled: bool = Field(default=False, alias="RTSP_GATEWAY_ENABLED")
    rtsp_gateway_listen_host: str = Field(default="0.0.0.0", alias="RTSP_GATEWAY_LISTEN_HOST")
    rtsp_gateway_port: int = Field(default=8554, alias="RTSP_GATEWAY_PORT")
    rtsp_gateway_wan_port: int = Field(default=55422, alias="RTSP_GATEWAY_WAN_PORT")
    mediamtx_binary: str = Field(default="mediamtx", alias="MEDIAMTX_BINARY")
    mediamtx_config_path: Path | None = Field(default=None, alias="MEDIAMTX_CONFIG_PATH")
    mediamtx_log_level: str = Field(default="info", alias="MEDIAMTX_LOG_LEVEL")

    # WebRTC (reservado Fase 2)
    webrtc_signaling_port: int = Field(default=8188, alias="WEBRTC_SIGNALING_PORT")
    webrtc_direct_rtsp: bool = Field(
        default=False,
        alias="WEBRTC_DIRECT_RTSP",
        description=(
            "Si true, WHEP usa una 2ª conexión RTSP (MediaPlayer) en lugar del búfer; "
            "útil si el visor sale negro pero la ingesta está OK"
        ),
    )

    # Búfer global (las cámaras pueden sobreescribir en buffer.duration_seconds)
    buffer_duration_seconds: float = Field(default=60.0, alias="BUFFER_DURATION_SECONDS")
    default_playback_offset_sec: float = Field(
        default=6.0, alias="DEFAULT_PLAYBACK_OFFSET_SEC"
    )
    default_playback_test_offset_sec: float = Field(
        default=3.0, alias="DEFAULT_PLAYBACK_TEST_OFFSET_SEC"
    )

    # Clip de evento nube (defaults; por cámara en buffer.event_*)
    pre_buffer_seconds: float = Field(default=6.0, alias="PRE_BUFFER_SECONDS")
    post_buffer_seconds: float = Field(default=24.0, alias="POST_BUFFER_SECONDS")
    stream_fps: int = Field(default=20, alias="STREAM_FPS")
    stream_width: int = Field(default=1280, alias="STREAM_WIDTH")
    stream_height: int = Field(default=720, alias="STREAM_HEIGHT")

    # Rutas de datos
    config_dir: Path = Field(default=Path("config"), alias="CONFIG_DIR")
    config_yaml_path: Path | None = Field(default=None, alias="CONFIG_YAML_PATH")
    cameras_json_path: Path | None = Field(default=None, alias="CAMERAS_JSON_PATH")
    cameras_db_path: Path | None = Field(default=None, alias="CAMERAS_DB_PATH")
    camera_store_backend: CameraStoreBackend = Field(
        default=CameraStoreBackend.JSON, alias="CAMERA_STORE_BACKEND"
    )

    # Descubrimiento
    discovery_enabled: bool = Field(default=False, alias="DISCOVERY_ENABLED")
    discovery_subnet: str = Field(default="192.168.1.0/24", alias="DISCOVERY_SUBNET")
    discovery_interval_seconds: int = Field(default=300, alias="DISCOVERY_INTERVAL_SECONDS")
    rtsp_scan_ports: list[int] = Field(default=[554, 8554], alias="RTSP_SCAN_PORTS")

    # Seguridad
    auth_mode: AuthMode = Field(default=AuthMode.API_KEY, alias="AUTH_MODE")
    api_key: SecretStr | None = Field(default=None, alias="API_KEY")
    jwt_secret: SecretStr | None = Field(default=None, alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")

    # UI web de instalación (Fase 4)
    webui_username: str = Field(default="admin", alias="WEBUI_USERNAME")
    webui_password: SecretStr | None = Field(default=None, alias="WEBUI_PASSWORD")
    webui_jwt_secret: SecretStr | None = Field(default=None, alias="WEBUI_JWT_SECRET")
    webui_token_expire_hours: int = Field(default=24, alias="WEBUI_TOKEN_EXPIRE_HOURS")

    # DDNS
    ddns_enabled: bool = Field(default=False, alias="DDNS_ENABLED")
    ddns_provider: DDNSProvider = Field(default=DDNSProvider.DUCKDNS, alias="DDNS_PROVIDER")
    ddns_hostname: str = Field(default="", alias="DDNS_HOSTNAME")
    ddns_token: SecretStr | None = Field(default=None, alias="DDNS_TOKEN")
    ddns_update_url: str = Field(default="", alias="DDNS_UPDATE_URL")
    ddns_interval_seconds: int = Field(default=300, alias="DDNS_INTERVAL_SECONDS")

    # Reporte IP a nube Kanvis (Fase 6)
    cloud_report_enabled: bool = Field(default=False, alias="CLOUD_REPORT_ENABLED")
    cloud_report_url: str = Field(
        default="",
        alias="CLOUD_REPORT_URL",
        description="POST report-public-ip (ej. …/api/v1/kanvis-edges/report-public-ip)",
    )
    cloud_access_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUD_ACCESS_TOKEN", "CLOUD_REPORT_TOKEN"),
        description="access_token del edge en Kanvis (C4); va en el body JSON, no Bearer",
    )
    cloud_report_on_ip_change_only: bool = Field(
        default=True, alias="CLOUD_REPORT_ON_IP_CHANGE_ONLY"
    )
    device_id: str = Field(default="", alias="DEVICE_ID")
    device_name: str = Field(
        default="",
        alias="DEVICE_NAME",
        description="Nombre legible de la instalación (tienda, ubicación, etc.)",
    )
    wan_sync_interval_seconds: int = Field(default=0, alias="WAN_SYNC_INTERVAL_SECONDS")

    # Reconexión RTSP / relay FFmpeg
    reconnect_base_delay: float = Field(default=1.0, alias="RECONNECT_BASE_DELAY")
    reconnect_max_delay: float = Field(default=60.0, alias="RECONNECT_MAX_DELAY")
    ffmpeg_path: str = Field(default="ffmpeg", alias="FFMPEG_PATH")
    ffmpeg_log_level: str = Field(default="warning", alias="FFMPEG_LOG_LEVEL")
    relay_transcode_preset: str = Field(default="veryfast", alias="RELAY_TRANSCODE_PRESET")
    snapshot_timeout_sec: float = Field(default=10.0, alias="SNAPSHOT_TIMEOUT_SEC")

    # Red / AP de instalación (Fase 5)
    network_mode: str = Field(default="ap_and_lan", alias="NETWORK_MODE")
    wlan_interface: str = Field(default="wlan0", alias="WLAN_INTERFACE")
    lan_interface: str = Field(default="eth0", alias="LAN_INTERFACE")
    ap_ssid_prefix: str = Field(default="kanvis", alias="AP_SSID_PREFIX")
    ap_ip: str = Field(default="192.168.192.192", alias="AP_IP")
    ap_password: SecretStr | None = Field(default=None, alias="AP_PASSWORD")
    install_root: Path = Field(default=Path("/opt/kanvis-edge"), alias="INSTALL_ROOT")

    @field_validator("rtsp_scan_ports", mode="before")
    @classmethod
    def parse_ports(cls, v: Any) -> list[int]:
        if isinstance(v, str):
            return [int(p.strip()) for p in v.split(",") if p.strip()]
        return v

    @property
    def resolved_config_yaml(self) -> Path:
        if self.config_yaml_path:
            return self.config_yaml_path
        return self.config_dir / "config.yaml"

    @property
    def resolved_cameras_json(self) -> Path:
        if self.cameras_json_path:
            return self.cameras_json_path
        return self.config_dir / "cameras.json"

    @property
    def resolved_cameras_db(self) -> Path:
        if self.cameras_db_path:
            return self.cameras_db_path
        return self.config_dir / "cameras.db"

    @property
    def resolved_mediamtx_config_path(self) -> Path:
        if self.mediamtx_config_path:
            return self.mediamtx_config_path
        return self.config_dir / "mediamtx.generated.yml"

    @property
    def buffer_max_packets_safety(self) -> int:
        """
        Límite duro de paquetes (protección RAM).
        H.264 suele enviar varios NAL por frame; con *3 el búfer se quedaba ~40 s.
        """
        pkt_per_sec = max(int(self.stream_fps) * 12, 120)
        return int(self.buffer_duration_seconds * pkt_per_sec)

    @property
    def effective_wan_sync_interval(self) -> int:
        """Intervalo del bucle DDNS + reporte nube."""
        if self.wan_sync_interval_seconds > 0:
            return self.wan_sync_interval_seconds
        return self.ddns_interval_seconds

    @property
    def wan_sync_enabled(self) -> bool:
        return self.ddns_enabled or self.cloud_report_enabled


_YAML_ENV_MAP: dict[tuple[str, str], str] = {
    ("gateway", "edge_api_host"): "EDGE_API_HOST",
    ("gateway", "edge_api_port"): "EDGE_API_PORT",
    ("gateway", "edge_panel_public_url"): "EDGE_PANEL_PUBLIC_URL",
    ("gateway", "log_level"): "LOG_LEVEL",
    ("buffer", "buffer_duration_seconds"): "BUFFER_DURATION_SECONDS",
    ("buffer", "default_playback_offset_sec"): "DEFAULT_PLAYBACK_OFFSET_SEC",
    ("buffer", "default_playback_test_offset_sec"): "DEFAULT_PLAYBACK_TEST_OFFSET_SEC",
    ("buffer", "pre_buffer_seconds"): "PRE_BUFFER_SECONDS",
    ("buffer", "post_buffer_seconds"): "POST_BUFFER_SECONDS",
    ("buffer", "stream_fps"): "STREAM_FPS",
    ("buffer", "stream_width"): "STREAM_WIDTH",
    ("buffer", "stream_height"): "STREAM_HEIGHT",
    ("relay", "edge_rtsp_host"): "EDGE_RTSP_HOST",
    ("relay", "edge_rtsp_port"): "EDGE_RTSP_PORT",
    ("rtsp_gateway", "rtsp_gateway_enabled"): "RTSP_GATEWAY_ENABLED",
    ("rtsp_gateway", "rtsp_gateway_listen_host"): "RTSP_GATEWAY_LISTEN_HOST",
    ("rtsp_gateway", "rtsp_gateway_port"): "RTSP_GATEWAY_PORT",
    ("rtsp_gateway", "rtsp_gateway_wan_port"): "RTSP_GATEWAY_WAN_PORT",
    ("rtsp_gateway", "mediamtx_binary"): "MEDIAMTX_BINARY",
    ("rtsp_gateway", "mediamtx_log_level"): "MEDIAMTX_LOG_LEVEL",
    ("webrtc", "webrtc_signaling_port"): "WEBRTC_SIGNALING_PORT",
    ("inventory", "camera_store_backend"): "CAMERA_STORE_BACKEND",
    ("discovery", "discovery_enabled"): "DISCOVERY_ENABLED",
    ("discovery", "discovery_subnet"): "DISCOVERY_SUBNET",
    ("discovery", "discovery_interval_seconds"): "DISCOVERY_INTERVAL_SECONDS",
    ("discovery", "rtsp_scan_ports"): "RTSP_SCAN_PORTS",
    ("security", "auth_mode"): "AUTH_MODE",
    ("security", "webui_username"): "WEBUI_USERNAME",
    ("security", "webui_token_expire_hours"): "WEBUI_TOKEN_EXPIRE_HOURS",
    ("ddns", "ddns_enabled"): "DDNS_ENABLED",
    ("ddns", "ddns_provider"): "DDNS_PROVIDER",
    ("ddns", "ddns_hostname"): "DDNS_HOSTNAME",
    ("ddns", "ddns_interval_seconds"): "DDNS_INTERVAL_SECONDS",
    ("cloud", "cloud_report_enabled"): "CLOUD_REPORT_ENABLED",
    ("cloud", "cloud_report_url"): "CLOUD_REPORT_URL",
    ("cloud", "cloud_report_on_ip_change_only"): "CLOUD_REPORT_ON_IP_CHANGE_ONLY",
    ("cloud", "device_id"): "DEVICE_ID",
    ("cloud", "device_name"): "DEVICE_NAME",
    ("cloud", "wan_sync_interval_seconds"): "WAN_SYNC_INTERVAL_SECONDS",
    ("rtsp_client", "reconnect_base_delay"): "RECONNECT_BASE_DELAY",
    ("rtsp_client", "reconnect_max_delay"): "RECONNECT_MAX_DELAY",
    ("rtsp_client", "ffmpeg_path"): "FFMPEG_PATH",
    ("rtsp_client", "ffmpeg_log_level"): "FFMPEG_LOG_LEVEL",
    ("rtsp_client", "relay_transcode_preset"): "RELAY_TRANSCODE_PRESET",
    ("network", "network_mode"): "NETWORK_MODE",
    ("network", "wlan_interface"): "WLAN_INTERFACE",
    ("network", "lan_interface"): "LAN_INTERFACE",
    ("network", "ap_ssid_prefix"): "AP_SSID_PREFIX",
    ("network", "ap_ip"): "AP_IP",
}


def _merge_yaml_into_env() -> None:
    """Carga config.yaml como valores por defecto si existen."""
    import os

    path = Path(os.getenv("CONFIG_YAML_PATH", "config/config.yaml"))
    if not path.is_file():
        path = Path("config/config.yaml")
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return

    for (section, key), env_name in _YAML_ENV_MAP.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        value = block.get(key)
        if value is None or env_name in os.environ:
            continue
        if isinstance(value, list):
            os.environ[env_name] = ",".join(str(v) for v in value)
        else:
            os.environ[env_name] = str(value)

    for key, value in data.items():
        if isinstance(value, dict):
            continue
        env_key = key.upper()
        if env_key not in os.environ and value is not None:
            os.environ[env_key] = str(value)


@lru_cache
def get_settings() -> AppSettings:
    """Singleton de configuración."""
    _merge_yaml_into_env()
    return AppSettings()
