"""Perfiles de marca VMS (plantillas RTSP) en config/brands/."""

from src.brands.models import BrandProfile, RtspProtocolSpec
from src.brands.registry import (
    default_brands_dir,
    list_brand_slugs,
    load_brand_profile,
    profile_matches_model,
    render_rtsp_url,
    rtsp_path_from_url,
)
from src.brands.rtsp_values import build_rtsp_template_values

__all__ = [
    "BrandProfile",
    "RtspProtocolSpec",
    "build_rtsp_template_values",
    "default_brands_dir",
    "list_brand_slugs",
    "load_brand_profile",
    "profile_matches_model",
    "render_rtsp_url",
    "rtsp_path_from_url",
]
