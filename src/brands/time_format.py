"""Conversión de patrones time_format del JSON a strftime."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

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


def parse_instant(
    value: str,
    *,
    time_format: str | None = None,
    requires_utc: bool = True,
) -> datetime:
    """Parsea marcas de tiempo de plantillas RTSP de fabricante."""
    text = value.strip()
    if time_format:
        pattern = strftime_pattern(time_format)
        if text.endswith("Z") and "[Z]" in time_format:
            text = text[:-1]
        if text.endswith("Z") and pattern.endswith("Z"):
            text = text[:-1]
        try:
            dt = datetime.strptime(text, pattern.replace("Z", ""))
        except ValueError as exc:
            raise ValueError(f"No se pudo parsear {value!r} con {time_format!r}") from exc
        if requires_utc:
            dt = dt.replace(tzinfo=timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return _parse_loose_instant(value)


_ISO_FALLBACK = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z?$",
    re.IGNORECASE,
)


def _parse_loose_instant(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        m = _ISO_FALLBACK.match(value.strip())
        if not m:
            raise ValueError(f"Marca de tiempo no reconocida: {value!r}") from None
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        dt = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_instant(
    dt: datetime,
    *,
    time_format: str,
    requires_utc: bool,
    time_offset_minutes: float = 0.0,
) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if time_offset_minutes:
        dt = dt + timedelta(minutes=float(time_offset_minutes))
    if requires_utc:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(strftime_pattern(time_format))
