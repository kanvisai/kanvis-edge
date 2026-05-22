"""Modelos de horario operativo (búfer + broadcast)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# Lunes=0 … Domingo=6 (datetime.weekday())
WEEKDAY_LABELS_ES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


class ScheduleWindow(BaseModel):
    """Franja horaria en un conjunto de días de la semana."""

    start: str = Field(description="Hora inicio HH:MM (24h)")
    end: str = Field(description="Hora fin HH:MM (24h, debe ser posterior al inicio)")
    days: list[int] = Field(
        default_factory=list,
        description="Días activos: 0=Lunes … 6=Domingo",
    )

    @field_validator("start", "end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        from src.schedule.evaluator import parse_hhmm

        parse_hhmm(v)
        return v

    @field_validator("days")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        for d in v:
            if d < 0 or d > 6:
                raise ValueError("days: cada valor debe estar entre 0 (lunes) y 6 (domingo)")
        return sorted(set(v))


class OperatingSchedule(BaseModel):
    """
    Horario global del edge.
    Si enabled=false o no hay ventanas, el sistema opera siempre (comportamiento legacy).
    """

    enabled: bool = False
    timezone: str = Field(
        default="",
        description="Zona IANA (ej. Europe/Madrid). Vacío = hora local del sistema",
    )
    windows: list[ScheduleWindow] = Field(default_factory=list)

    def model_dump_for_storage(self) -> dict:
        return self.model_dump(mode="json")
