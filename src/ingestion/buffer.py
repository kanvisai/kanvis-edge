"""PacketCircularBuffer: búfer por tiempo real (H.264/H.265 sin decodificar)."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True, slots=True)
class RawPacket:
    """Paquete demuxado (codec copy, sin RGB/NumPy)."""

    data: bytes
    pts: int | None
    dts: int | None
    is_keyframe: bool
    time_base_num: int = 1
    time_base_den: int = 90000
    captured_at: float = field(default_factory=time.monotonic)


class PacketCircularBuffer:
    """
    Búfer circular en RAM recortado por duración en segundos (monotonic).
    Thread-safe para StreamConsumer y VideoDispatcher.
    """

    def __init__(
        self,
        max_duration_seconds: float,
        max_packets_safety: int = 50_000,
    ) -> None:
        self._max_duration = max_duration_seconds
        self._max_packets_safety = max_packets_safety
        self._deque: deque[RawPacket] = deque()
        self._lock = threading.Lock()

    @property
    def max_duration_seconds(self) -> float:
        return self._max_duration

    def set_max_duration(self, seconds: float) -> None:
        with self._lock:
            self._max_duration = max(1.0, seconds)
            self._prune_locked(time.monotonic())

    def append(self, packet: RawPacket) -> None:
        with self._lock:
            self._deque.append(packet)
            self._prune_locked(packet.captured_at)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._max_duration
        while self._deque and self._deque[0].captured_at < cutoff:
            self._deque.popleft()
        while len(self._deque) > self._max_packets_safety:
            self._deque.popleft()

    def size(self) -> int:
        with self._lock:
            return len(self._deque)

    def span_seconds(self) -> float:
        """Duración real cubierta por el búfer (0 si vacío)."""
        with self._lock:
            if not self._deque:
                return 0.0
            return self._deque[-1].captured_at - self._deque[0].captured_at

    def snapshot(self) -> list[RawPacket]:
        with self._lock:
            return list(self._deque)

    def snapshot_last_seconds(self, seconds: float) -> list[RawPacket]:
        """Ventana [ahora - seconds, ahora] sin vaciar el búfer."""
        return self.snapshot_between_ages(seconds, 0.0)

    def snapshot_between_ages(
        self,
        start_sec_ago: float,
        end_sec_ago: float,
    ) -> list[RawPacket]:
        """
        Paquetes entre end_sec_ago y start_sec_ago en el pasado (orden cronológico).

        Ej.: start_sec_ago=6, end_sec_ago=0 → últimos 6 s hasta ahora.
        """
        if start_sec_ago < end_sec_ago:
            start_sec_ago, end_sec_ago = end_sec_ago, start_sec_ago
        now = time.monotonic()
        cutoff_old = now - max(0.0, start_sec_ago)
        cutoff_new = now - max(0.0, end_sec_ago)
        with self._lock:
            return [p for p in self._deque if cutoff_old <= p.captured_at <= cutoff_new]

    def snapshot_from_preceding_keyframe(
        self,
        start_sec_ago: float,
        end_sec_ago: float,
    ) -> list[RawPacket]:
        """
        Devuelve paquetes empezando desde el keyframe más cercano al inicio
        del rango solicitado, buscando primero hacia atrás y si no hay,
        hacia adelante dentro del rango.

        Prioridad:
          1. Último keyframe AT o ANTES de start_sec_ago (extiende hacia atrás)
          2. Primer keyframe DESPUÉS de start_sec_ago (dentro del rango)
          3. Inicio del rango sin keyframe (último recurso, con warning externo)
        """
        if start_sec_ago < end_sec_ago:
            start_sec_ago, end_sec_ago = end_sec_ago, start_sec_ago
        now = time.monotonic()
        cutoff_old = now - max(0.0, start_sec_ago)
        cutoff_new = now - max(0.0, end_sec_ago)
        with self._lock:
            kf_before: int | None = None
            kf_in_range: int | None = None
            first_in_range: int | None = None

            for i, p in enumerate(self._deque):
                if p.captured_at > cutoff_new:
                    break
                if p.captured_at <= cutoff_old:
                    if p.is_keyframe:
                        kf_before = i
                else:
                    if first_in_range is None:
                        first_in_range = i
                    if kf_in_range is None and p.is_keyframe:
                        kf_in_range = i

            if kf_before is not None:
                start_idx = kf_before
            elif kf_in_range is not None:
                start_idx = kf_in_range
            elif first_in_range is not None:
                start_idx = first_in_range
            else:
                return []

            return [
                p for p in list(self._deque)[start_idx:]
                if p.captured_at <= cutoff_new
            ]

    def drain_atomic(self) -> list[RawPacket]:
        """Vacía todo el búfer (uso legacy; preferir snapshot_last_seconds)."""
        with self._lock:
            drained = list(self._deque)
            self._deque.clear()
        return drained

    def iter_live(self) -> Iterator[RawPacket]:
        raise NotImplementedError("Usar VideoDispatcher para streaming en vivo")
