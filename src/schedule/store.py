"""Persistencia de operating_schedule.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.schedule.models import OperatingSchedule

logger = logging.getLogger(__name__)

DEFAULT_SCHEDULE = OperatingSchedule(enabled=False, windows=[])


def schedule_file_path(config_dir: Path) -> Path:
    return config_dir / "operating_schedule.json"


def load_schedule(path: Path) -> OperatingSchedule:
    if not path.is_file():
        return DEFAULT_SCHEDULE.model_copy(deep=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return OperatingSchedule.model_validate(raw)
    except Exception:
        logger.exception("No se pudo leer %s; usando horario por defecto", path)
        return DEFAULT_SCHEDULE.model_copy(deep=True)


def save_schedule(path: Path, schedule: OperatingSchedule) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    data = json.dumps(schedule.model_dump_for_storage(), indent=2, ensure_ascii=False)
    tmp.write_text(data + "\n", encoding="utf-8")
    tmp.replace(path)
