"""Evaluación de ventanas horarias."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.schedule.models import OperatingSchedule, ScheduleWindow

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(value: str) -> time:
    m = _HHMM_RE.match(value.strip())
    if not m:
        raise ValueError(f"Hora inválida '{value}'; usa HH:MM (ej. 08:50)")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"Hora fuera de rango: {value}")
    return time(hour=hour, minute=minute)


def _resolve_now(now: datetime | None, timezone: str) -> datetime:
    if now is not None:
        return now
    if timezone:
        try:
            tz = ZoneInfo(timezone)
        except Exception as exc:
            raise ValueError(f"Zona horaria inválida: {timezone}") from exc
        return datetime.now(tz)
    return datetime.now().astimezone()


def _window_active(window: ScheduleWindow, now: datetime) -> bool:
    if now.weekday() not in window.days:
        return False
    start = parse_hhmm(window.start)
    end = parse_hhmm(window.end)
    current = now.time().replace(microsecond=0)
    if start < end:
        return start <= current < end
    # Ventana nocturna (ej. 22:00 → 06:00)
    return current >= start or current < end


def is_operating_now(
    schedule: OperatingSchedule,
    now: datetime | None = None,
) -> bool:
    """True si el horario permite ingesta/búfer y broadcast."""
    if not schedule.enabled:
        return True
    if not schedule.windows:
        return True
    dt = _resolve_now(now, schedule.timezone)
    return any(_window_active(w, dt) for w in schedule.windows)


def schedule_status(
    schedule: OperatingSchedule,
    now: datetime | None = None,
) -> dict:
    """Estado legible para API/UI."""
    dt = _resolve_now(now, schedule.timezone) if schedule.enabled and schedule.windows else None
    active = is_operating_now(schedule, dt)
    return {
        "enabled": schedule.enabled,
        "timezone": schedule.timezone or None,
        "windows_count": len(schedule.windows),
        "is_active_now": active,
        "local_time": dt.isoformat(timespec="seconds") if dt else None,
        "weekday": dt.weekday() if dt else None,
        "weekday_label": (
            ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")[dt.weekday()]
            if dt is not None
            else None
        ),
    }
