"""Métricas de ingesta RTSP por cámara."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class IngestMetrics:
    packets_total: int = 0
    bytes_total: int = 0
    errors_total: int = 0
    connected: bool = False
    last_packet_at: float | None = None
    connected_since: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_connected(self) -> None:
        now = time.monotonic()
        with self._lock:
            self.connected = True
            self.connected_since = now

    def on_disconnected(self) -> None:
        with self._lock:
            self.connected = False
            self.connected_since = None

    def on_packet(self, size: int) -> None:
        now = time.monotonic()
        with self._lock:
            self.packets_total += 1
            self.bytes_total += size
            self.last_packet_at = now

    def on_error(self) -> None:
        with self._lock:
            self.errors_total += 1
            self.connected = False

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            idle_sec = (
                (now - self.last_packet_at) if self.last_packet_at is not None else None
            )
            uptime = (
                (now - self.connected_since)
                if self.connected and self.connected_since
                else 0.0
            )
            return {
                "connected": self.connected,
                "packets_total": self.packets_total,
                "bytes_total": self.bytes_total,
                "errors_total": self.errors_total,
                "last_packet_idle_sec": round(idle_sec, 2) if idle_sec is not None else None,
                "connected_uptime_sec": round(uptime, 2),
            }
