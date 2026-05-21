"""Entrypoint del Kanvis Edge Video Gateway."""

from __future__ import annotations

import uvicorn

from src.api.app import create_app
from src.config_loader import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.edge_api_host,
        port=settings.edge_api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
