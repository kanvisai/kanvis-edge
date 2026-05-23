"""Carga de perfiles JSON y renderizado de plantillas RTSP."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

from src.brands.models import BrandProfile
from src.brands.time_format import format_instant

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def default_brands_dir(config_dir: Path | None = None) -> Path:
    env = os.getenv("KANVIS_BRANDS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if config_dir is not None:
        candidate = (config_dir / "brands").resolve()
        if candidate.is_dir():
            return candidate
    cwd_candidate = Path.cwd() / "config" / "brands"
    if cwd_candidate.is_dir():
        return cwd_candidate.resolve()
    app_candidate = Path("/opt/kanvis-edge/config/brands")
    if app_candidate.is_dir():
        return app_candidate
    return cwd_candidate.resolve()


def list_brand_slugs(brands_dir: Path | None = None) -> list[str]:
    root = brands_dir or default_brands_dir()
    if not root.is_dir():
        return []
    slugs: list[str] = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() == ".json" and path.is_file():
            slugs.append(path.stem.lower())
    return slugs


@lru_cache(maxsize=32)
def _load_profile_cached(path_str: str, mtime_ns: int) -> BrandProfile:
    _ = mtime_ns
    path = Path(path_str)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Brand profile must be a mapping: {path}")
    return BrandProfile.model_validate(data)


def load_brand_profile(slug: str, brands_dir: Path | None = None) -> BrandProfile:
    root = brands_dir or default_brands_dir()
    key = slug.strip().lower()
    path = root / f"{key}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Brand profile not found for slug={key!r} under {root}")
    return _load_profile_cached(str(path.resolve()), path.stat().st_mtime_ns)


def render_rtsp_url(
    profile: BrandProfile,
    *,
    mode: Literal["stream", "playback"],
    values: dict[str, Any],
    starttime: datetime | None = None,
    endtime: datetime | None = None,
    time_offset_minutes: float = 0.0,
) -> str:
    rtsp = profile.protocols.rtsp
    template = rtsp.stream_template if mode == "stream" else rtsp.playback_template
    merged = dict(values)
    if mode == "playback":
        if starttime is None or endtime is None:
            raise ValueError("playback mode requires starttime and endtime")
        merged["starttime"] = quote(
            format_instant(
                starttime,
                time_format=rtsp.time_format,
                requires_utc=rtsp.requires_utc,
                time_offset_minutes=time_offset_minutes,
            ),
            safe="-T",
        )
        merged["endtime"] = quote(
            format_instant(
                endtime,
                time_format=rtsp.time_format,
                requires_utc=rtsp.requires_utc,
                time_offset_minutes=time_offset_minutes,
            ),
            safe="-T",
        )
    return _substitute(template, merged)


def profile_matches_model(profile: BrandProfile, model: str | None) -> tuple[bool, str]:
    if not profile.models:
        return True, ""
    key = (model or "").strip()
    if not key:
        return False, "model is required for this brand profile (models list is not empty)"
    if key in profile.models:
        return True, ""
    return False, f"model {key!r} not in profile.models {profile.models!r}"


def rtsp_path_from_url(url: str) -> str:
    """Ruta RTSP sin barra inicial (p. ej. Streaming/channels/101)."""
    parsed = urlparse(url)
    return (parsed.path or "/").lstrip("/")


def _substitute(template: str, values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"Missing placeholder {{{{{key}}}}} for template")
        return str(values[key])

    return _PLACEHOLDER_RE.sub(repl, template)
