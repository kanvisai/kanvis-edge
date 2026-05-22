from __future__ import annotations

from pydantic import BaseModel, Field


class RtspProtocolSpec(BaseModel):
    stream_template: str
    playback_template: str
    time_format: str
    requires_utc: bool = False


class BrandProtocols(BaseModel):
    rtsp: RtspProtocolSpec


class BrandChannelHint(BaseModel):
    id: str
    label: str = ""


class BrandProfile(BaseModel):
    brand: str
    version: str
    models: list[str] = Field(default_factory=list)
    protocols: BrandProtocols
    default_channels: list[BrandChannelHint] = Field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.brand.strip().lower()
