"""Servicio de horario operativo (mutable en caliente)."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from src.schedule.evaluator import is_operating_now, schedule_status
from src.schedule.models import OperatingSchedule
from src.schedule.store import load_schedule, save_schedule, schedule_file_path


class OperatingScheduleService:
    def __init__(self, config_dir: Path) -> None:
        self._path = schedule_file_path(config_dir)
        self._lock = threading.Lock()
        self._schedule = load_schedule(self._path)

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> OperatingSchedule:
        with self._lock:
            return self._schedule.model_copy(deep=True)

    def get_status(self, now: datetime | None = None) -> dict:
        with self._lock:
            sched = self._schedule.model_copy(deep=True)
        base = schedule_status(sched, now)
        base["path"] = str(self._path)
        return base

    def is_operating_now(self, now: datetime | None = None) -> bool:
        with self._lock:
            sched = self._schedule
        return is_operating_now(sched, now)

    def update(self, schedule: OperatingSchedule) -> OperatingSchedule:
        # Valida ventanas (pydantic + parse_hhmm en modelo)
        validated = OperatingSchedule.model_validate(schedule.model_dump())
        with self._lock:
            self._schedule = validated
            save_schedule(self._path, validated)
        return validated.model_copy(deep=True)

    def reload(self) -> OperatingSchedule:
        with self._lock:
            self._schedule = load_schedule(self._path)
            return self._schedule.model_copy(deep=True)


def require_operating_now(service: OperatingScheduleService | None) -> None:
    if service is None:
        return
    if service.is_operating_now():
        return
    st = service.get_status()
    when = ""
    if st.get("local_time"):
        when = f" Hora local edge: {st['local_time'].replace('T', ' ')} ({st.get('weekday_label') or ''})."
    raise HTTPException(
        status_code=503,
        detail=(
            "Fuera del horario operativo (búfer y broadcast bloqueados)."
            f"{when} En el panel: pestaña Sistema → desmarca «Activar horario» "
            "o amplía las franjas y guarda."
        ),
    )
