"""GatewayController: aplicación FastAPI."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.dispatcher import VideoDispatcher
from src.api.routes import router
from src.api.webui_routes import STATIC_DIR, router as webui_router
from src.api.testing_routes import router as testing_router
from src.api.webrtc_routes import router as webrtc_router
from src.config_loader import AppSettings, get_settings
from src.discovery.repository import create_camera_repository
from src.discovery.scanner import NetworkScanner
from src.ingestion.consumer import StreamConsumerManager
from src.gateway.manager import GatewayManager
from src.relay.manager import RelayManager
from src.webrtc.manager import WebRtcManager
from src.schedule.service import OperatingScheduleService
from src.services.public_ip import PublicIpService
from src.services.wan_sync import WanSyncService
from src.services.security import SecurityManager

logger = logging.getLogger(__name__)


async def _wan_sync_boot(wan_sync: WanSyncService) -> None:
    try:
        await wan_sync.sync_once(force_cloud=True)
    except Exception:
        logger.exception("WAN sync inicial falló")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: AppSettings = app.state.settings
    loop = __import__("asyncio").get_running_loop()

    repo = app.state.camera_repository
    schedule_service = app.state.operating_schedule_service
    consumer_manager = StreamConsumerManager(settings, repo, schedule_service)
    relay_manager = RelayManager(settings, repo, schedule_service)
    gateway_manager = GatewayManager(settings, repo)
    webrtc_manager = WebRtcManager(settings, repo, consumer_manager)
    await consumer_manager.sync_from_repository(loop)
    await relay_manager.sync_from_repository()
    await gateway_manager.sync_from_repository()
    await webrtc_manager.sync_from_repository()
    app.state.consumer_manager = consumer_manager
    app.state.relay_manager = relay_manager
    app.state.gateway_manager = gateway_manager
    app.state.webrtc_manager = webrtc_manager
    app.state.video_dispatcher = VideoDispatcher(settings, consumer_manager)

    tasks = []

    async def _inventory_loop() -> None:
        while True:
            try:
                await consumer_manager.sync_from_repository(loop)
                await relay_manager.sync_from_repository()
                await gateway_manager.sync_from_repository()
                await webrtc_manager.sync_from_repository()
            except Exception:
                logger.exception("Error sincronizando inventario")
            await asyncio.sleep(30)

    tasks.append(loop.create_task(_inventory_loop()))

    public_ip_service = PublicIpService(settings)
    app.state.public_ip_service = public_ip_service
    try:
        await public_ip_service.refresh()
    except Exception:
        logger.warning(
            "IP pública no disponible al arranque; se reintentará en segundo plano"
        )
    tasks.append(loop.create_task(public_ip_service.run_loop()))

    if settings.discovery_enabled:
        scanner = NetworkScanner(settings, app.state.camera_repository)
        app.state.network_scanner = scanner
        tasks.append(loop.create_task(scanner.run_loop()))

    if settings.wan_sync_enabled:
        wan_sync = WanSyncService(settings)
        app.state.wan_sync_service = wan_sync
        app.state.ddns_service = wan_sync  # alias retrocompatible
        tasks.append(loop.create_task(wan_sync.run_loop()))
        # Sincronización al arranque (no bloquear lifespan)
        tasks.append(loop.create_task(_wan_sync_boot(wan_sync)))

    logger.info("Kanvis Edge Gateway iniciado en :%s", settings.edge_api_port)
    yield

    for task in tasks:
        task.cancel()
    if hasattr(app.state, "public_ip_service"):
        app.state.public_ip_service.stop()
    consumer_manager.shutdown_all()
    relay_manager.shutdown_all()
    await gateway_manager.shutdown()
    await webrtc_manager.shutdown_all()
    logger.info("Kanvis Edge Gateway detenido")


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = FastAPI(
        title="Kanvis Edge Video Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.camera_repository = create_camera_repository(settings)
    app.state.operating_schedule_service = OperatingScheduleService(settings.config_dir)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    security = SecurityManager(settings)
    app.middleware("http")(security.middleware)

    app.include_router(webui_router)
    app.include_router(router)
    app.include_router(webrtc_router)
    app.include_router(testing_router)
    return app
