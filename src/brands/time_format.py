"""Conversión de patrones time_format del JSON a strftime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_TIME_FORMAT_TO_STRFTIME: dict[str, str] = {
    "YYYY-MM-DDTHH:mm:ss[Z]": "%Y-%m-%dT%H:%M:%SZ",
    "YYYY-MM-DDTHH:mm:ssZ": "%Y-%m-%dT%H:%M:%SZ",
    "YYYYmmddTHHmmssZ": "%Y%m%dT%H%M%SZ",
    "YYYY-MM-DDTHH:mm:ss": "%Y-%m-%dT%H:%M:%S",
    "YYYY_MM_DD_HH_mm_ss": "%Y_%m_%d_%H_%M_%S",
}


def strftime_pattern(time_format: str) -> str:
    key = time_format.strip()
    if key not in _TIME_FORMAT_TO_STRFTIME:
        raise ValueError(
            f"Unsupported time_format {time_format!r}; supported: {sorted(_TIME_FORMAT_TO_STRFTIME)}"
        )
    return _TIME_FORMAT_TO_STRFTIME[key]


def format_instant(
    dt: datetime,
    *,
    time_format: str,
    requires_utc: bool,
    time_offset_minutes: float = 0.0,
) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if time_offset_minutes:
        dt = dt + timedelta(minutes=float(time_offset_minutes))
    if requires_utc:
        dt = dt.astimezone(UTC)
    return dt.strftime(strftime_pattern(time_format))
