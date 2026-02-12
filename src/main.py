"""
Main entry point for the ANPR Edge Device.

Wires together all services and starts the FastAPI webhook receiver.

Usage:
    python -m main                   # Uses config/config.yaml
    EDGE_CONFIG_PATH=... python -m main  # Custom config path
"""
import logging
import signal
import sys
import os
import threading
import time

import uvicorn

from config import load_settings
from database.repository import Repository
from hardware import get_hardware_interface
from hardware.base import RelayConfig
from services import (
    AccessDecisionEngine,
    CloudSyncService,
    EventProcessor,
    HeartbeatService,
)
from api import create_app

logger = logging.getLogger(__name__)


def setup_logging(level: str, log_file: str) -> None:
    """Configure logging for the application"""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


def main() -> None:
    # Load settings
    settings = load_settings()

    # Setup logging
    setup_logging(settings.logging.level, settings.logging.file)
    logger.info("=" * 60)
    logger.info("ANPR Edge Device starting...")
    logger.info(f"Controller ID: {settings.device.controller_id}")
    logger.info("=" * 60)

    # Validate required settings
    if not settings.device.controller_id:
        logger.error("EDGE_CONTROLLER_ID is required. Set in config.yaml or env var.")
        sys.exit(1)
    if not settings.device.api_key:
        logger.error("EDGE_API_KEY is required. Set in config.yaml or env var.")
        sys.exit(1)

    # Initialize database
    os.makedirs(os.path.dirname(settings.database.path), exist_ok=True)
    repo = Repository(database_url=settings.database.url)
    logger.info("Database initialized")

    # Initialize hardware
    try:
        hardware = get_hardware_interface()
        hardware.setup_gpio(RelayConfig(
            pin=settings.gpio.relay_pin,
            pulse_duration=settings.gpio.pulse_duration,
            active_high=settings.gpio.active_high,
        ))
        logger.info("Hardware interface initialized")
    except (ValueError, RuntimeError) as e:
        logger.error(f"Hardware initialization failed: {e}")
        logger.info("Continuing without hardware (gate control disabled)")
        hardware = None

    # Initialize services
    access_engine = AccessDecisionEngine(repository=repo)

    heartbeat_service = HeartbeatService(
        repository=repo,
        hardware=hardware,
        controller_id=settings.device.controller_id,
        api_key=settings.device.api_key,
        api_base_url=settings.api.base_url,
        interval=settings.sync.heartbeat_interval,
        stream_health_timeout=settings.stream.health_timeout,
    )

    cloud_sync = CloudSyncService(
        repository=repo,
        controller_id=settings.device.controller_id,
        api_key=settings.device.api_key,
        api_base_url=settings.api.base_url,
        config_interval=settings.sync.config_sync_interval,
        event_interval=settings.sync.event_upload_interval,
        event_batch_size=settings.sync.event_batch_size,
    )

    event_processor = EventProcessor(
        repository=repo,
        hardware=hardware,
        access_engine=access_engine,
        heartbeat_service=heartbeat_service,
    )

    # Create FastAPI app
    app = create_app(
        event_processor=event_processor,
        heartbeat_service=heartbeat_service,
        cloud_sync=cloud_sync,
    )

    # Start background services
    heartbeat_service.start()
    cloud_sync.start()

    # Initial config sync from cloud
    logger.info("Performing initial config sync...")
    cloud_sync.sync_config_now()

    # Periodic DB cleanup (acked events older than 48h, stale plate readings)
    def _cleanup_loop():
        while True:
            time.sleep(3600)
            try:
                repo.run_cleanup()
                logger.debug("Database cleanup completed")
            except Exception as e:
                logger.error(f"Database cleanup failed: {e}")

    threading.Thread(target=_cleanup_loop, daemon=True).start()

    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received, stopping services...")
        heartbeat_service.stop()
        cloud_sync.stop()
        if hardware:
            hardware.cleanup()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Start the webhook server
    logger.info(f"Starting webhook server on port {settings.stream.webhook_port}")
    logger.info(f"Webhook endpoint: http://0.0.0.0:{settings.stream.webhook_port}{settings.stream.webhook_path}")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.stream.webhook_port,
        log_level=settings.logging.level.lower(),
    )


if __name__ == "__main__":
    main()
