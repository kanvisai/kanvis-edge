"""Tests de horario operativo."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.schedule.evaluator import is_operating_now, parse_hhmm
from src.schedule.models import OperatingSchedule, ScheduleWindow


def test_parse_hhmm() -> None:
    assert parse_hhmm("08:50") == parse_hhmm("8:50")


def test_disabled_schedule_always_active() -> None:
    sched = OperatingSchedule(enabled=False, windows=[])
    assert is_operating_now(sched)


def test_weekday_window_active() -> None:
    sched = OperatingSchedule(
        enabled=True,
        windows=[
            ScheduleWindow(start="08:50", end="14:05", days=[0, 1, 2, 3, 4, 5]),
        ],
    )
    # Lunes 10:00
    mon = datetime(2026, 5, 18, 10, 0, 0)
    assert mon.weekday() == 0
    assert is_operating_now(sched, mon)

    # Domingo 10:00 — sin ventana
    sun = datetime(2026, 5, 17, 10, 0, 0)
    assert sun.weekday() == 6
    assert not is_operating_now(sched, sun)

    # Lunes 23:00 — fuera de franja
    mon_night = datetime(2026, 5, 18, 23, 0, 0)
    assert not is_operating_now(sched, mon_night)


def test_two_windows_same_day() -> None:
    sched = OperatingSchedule(
        enabled=True,
        windows=[
            ScheduleWindow(start="08:50", end="14:05", days=[0]),
            ScheduleWindow(start="16:55", end="21:05", days=[0]),
        ],
    )
    mon_morning = datetime(2026, 5, 18, 9, 0, 0)
    mon_gap = datetime(2026, 5, 18, 15, 0, 0)
    mon_evening = datetime(2026, 5, 18, 20, 0, 0)
    assert is_operating_now(sched, mon_morning)
    assert not is_operating_now(sched, mon_gap)
    assert is_operating_now(sched, mon_evening)


def test_invalid_time_raises() -> None:
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
