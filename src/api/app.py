"""
FastAPI application factory for edge device webhook receiver.

Receives webhooks from Plate Recognizer Stream running on the same device
and exposes a local management API for manual overrides and status.
"""
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .routes import router

logger = logging.getLogger(__name__)


def create_app(event_processor=None, heartbeat_service=None, cloud_sync=None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        event_processor: EventProcessor instance for handling webhooks
        heartbeat_service: HeartbeatService instance for status endpoint
        cloud_sync: CloudSyncService instance for manual sync triggers
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Edge controller API started")
        yield
        logger.info("Edge controller API shutting down")

    # Disable docs in production unless explicitly enabled
    enable_docs = os.environ.get('EDGE_ENABLE_DOCS', '').lower() in ('1', 'true')

    app = FastAPI(
        title="ANPR Edge Controller",
        description="Local webhook receiver for Plate Recognizer Stream",
        version="1.0.0",
        docs_url="/docs" if enable_docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Store services in app state so routes can access them
    app.state.event_processor = event_processor
    app.state.heartbeat_service = heartbeat_service
    app.state.cloud_sync = cloud_sync

    app.include_router(router)

    return app
