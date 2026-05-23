"""Parseo de query RTSP playback (starttime/endtime, varios fabricantes)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlencode

from src.brands.time_format import parse_instant

if TYPE_CHECKING:
    from starlette.requests import Request

    from src.brands.models import BrandProfile

_START_KEYS = ("starttime", "start", "begin", "from")
_END_KEYS = ("endtime", "end", "to", "until")


def _first_param(query: dict[str, list[str]], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in query and query[key]:
            raw = query[key][0].strip()
            if raw:
                return raw
        alt = key.lower()
        for qk, vals in query.items():
            if qk.lower() == alt and vals and vals[0].strip():
                return vals[0].strip()
    return None


def playback_query_from_request(request: Request, mtx_query: str = "") -> str:
    """
    Query de playback para parse_playback_query_string.

    MediaMTX/FFmpeg pasan starttime y endtime como params sueltos
    (?mtx_path=…&starttime=…&endtime=…), no empaquetados en mtx_query=.
    """
    raw = (mtx_query or "").strip()
    if raw:
        return raw
    params = parse_qs(request.url.query, keep_blank_values=False)
    params.pop("mtx_path", None)
    entries = params.pop("mtx_query", None)
    if not params and entries:
        return entries[0]
    if not params:
        return ""
    flat = {k: v[0] for k, v in params.items() if v and v[0].strip()}
    return urlencode(flat, safe=":-T%Z")


def parse_playback_query_string(
    query: str,
    *,
    profile: BrandProfile | None = None,
) -> tuple[datetime, datetime]:
    """Devuelve (start, end) en UTC a partir de MTX_QUERY o query de URL."""
    raw = query.strip()
    if not raw:
        raise ValueError("Faltan parámetros de playback en la URL")
    parsed = parse_qs(raw, keep_blank_values=False)
    start_s = _first_param(parsed, _START_KEYS)
    end_s = _first_param(parsed, _END_KEYS)
    if not start_s or not end_s:
        raise ValueError("La URL de playback requiere starttime y endtime")

    time_format = None
    requires_utc = True
    offset_min = 0.0
    if profile is not None:
        rtsp = profile.protocols.rtsp
        time_format = rtsp.time_format
        requires_utc = rtsp.requires_utc

    start = parse_instant(
        start_s,
        time_format=time_format,
        requires_utc=requires_utc,
    )
    end = parse_instant(
        end_s,
        time_format=time_format,
        requires_utc=requires_utc,
    )
    if offset_min:
        from datetime import timedelta

        delta = timedelta(minutes=float(offset_min))
        start -= delta
        end -= delta
    return start, end


def normalize_gateway_path(path: str) -> str:
    """Ruta sin barra inicial ni query (p. ej. Streaming/tracks/101)."""
    p = unquote((path or "").strip())
    if "?" in p:
        p = p.split("?", 1)[0]
    return p.strip("/")


def parse_playback_path_and_query(
    path: str,
    query: str,
    *,
    profile: BrandProfile | None = None,
) -> tuple[datetime, datetime]:
    combined = (path or "").strip()
    if "?" in combined and not query.strip():
        path_part, query = combined.split("?", 1)
        path = path_part
    return parse_playback_query_string(query, profile=profile)


