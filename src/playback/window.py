"""Planificación de ventanas playback (búfer / cámara / cola en vivo)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class PlaybackPlan:
    """Desglose de una petición playback en fases ejecutables."""

    start: datetime
    end: datetime
    requested_at: datetime
    buffer_start_sec_ago: float
    buffer_end_sec_ago: float
    live_tail_sec: float
    camera_start: datetime | None
    camera_end: datetime | None

    @property
    def needs_buffer(self) -> bool:
        return self.buffer_start_sec_ago > self.buffer_end_sec_ago + 0.01

    @property
    def needs_camera(self) -> bool:
        return self.camera_start is not None and self.camera_end is not None

    @property
    def needs_live_tail(self) -> bool:
        return self.live_tail_sec > 0.01


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def plan_playback_window(
    *,
    start: datetime,
    end: datetime,
    requested_at: datetime | None = None,
    buffer_depth_sec: float,
) -> PlaybackPlan:
    """
    Calcula qué trozos servir desde búfer RAM, grabación de la cámara o vivo.

    - Búfer: tramo reciente [start, min(end, ahora)] dentro de la profundidad del búfer.
    - Vivo: si end > ahora, emite en directo hasta end.
    - Cámara: histórico anterior al búfer (la cámara no suele dar playback reciente).
    """
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    now = _ensure_utc(requested_at or datetime.now(timezone.utc))

    if end <= start:
        raise ValueError("endtime debe ser posterior a starttime")

    depth = max(1.0, float(buffer_depth_sec))
    age_start = (now - start).total_seconds()
    age_end = (now - end).total_seconds()

    if age_start < -0.05:
        raise ValueError("starttime no puede estar en el futuro")

    # Histórico hasta «ahora» (segundos en el pasado; 0 = instante actual)
    hist_end_sec_ago = max(0.0, age_end) if end <= now else 0.0
    hist_start_sec_ago = max(0.0, age_start)

    buffer_start_sec_ago = 0.0
    buffer_end_sec_ago = 0.0
    if hist_start_sec_ago > 0.05 and hist_end_sec_ago < depth - 0.05:
        buffer_start_sec_ago = min(hist_start_sec_ago, depth)
        buffer_end_sec_ago = hist_end_sec_ago

    live_tail_sec = max(0.0, (end - now).total_seconds()) if end > now else 0.0

    camera_start: datetime | None = None
    camera_end: datetime | None = None
    if hist_start_sec_ago > depth + 0.05:
        camera_start = start
        hist_end_wall = min(end, now) if end <= now else now - timedelta(seconds=depth)
        camera_wall_end = min(hist_end_wall, now - timedelta(seconds=depth))
        if camera_wall_end > camera_start + timedelta(milliseconds=200):
            camera_end = camera_wall_end

    return PlaybackPlan(
        start=start,
        end=end,
        requested_at=now,
        buffer_start_sec_ago=buffer_start_sec_ago,
        buffer_end_sec_ago=buffer_end_sec_ago,
        live_tail_sec=live_tail_sec,
        camera_start=camera_start,
        camera_end=camera_end,
    )
