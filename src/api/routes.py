"""
API routes for the edge device.

Endpoints:
- GET  /healthz              — Simple health check for Docker/monitoring
- POST /v1/webhook/stream    — Receives webhooks from Plate Recognizer Stream
- POST /v1/override          — Manual gate override from local UI/button
- GET  /v1/status            — Device health and sync status
- POST /v1/sync              — Force immediate cloud config sync
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
async def health_check():
    """Simple health check for Docker/load balancers."""
    return {"status": "ok"}


@router.post("/v1/webhook/stream")
async def receive_stream_webhook(request: Request):
    """
    Receive plate detection webhook from Plate Recognizer Stream (on-premise).

    Stream webhook format:
    {
        "hook": {
            "event": "recognition",
            "id": "camera-entry-1",
            "target": "http://localhost:8001/v1/webhook/stream"
        },
        "data": {
            "results": [{
                "plate": "abc123",
                "score": 0.906,
                "box": {"xmin": 153, "ymin": 91, "xmax": 302, "ymax": 125},
                "region": {"code": "us", "score": 0.476},
                "vehicle": {"type": "SUV", "score": 0.254}
            }],
            "camera_id": "camera-entry-1",
            "timestamp": "2025-02-08T10:30:45.161444Z",
            "timestamp_local": "2025-02-08 10:30:45.161444+00:00"
        }
    }
    """
    processor = request.app.state.event_processor
    if not processor:
        return JSONResponse(
            status_code=503,
            content={"error": "Event processor not initialized"},
        )

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON payload"},
        )

    result = processor.process_stream_webhook(payload)
    return result


@router.post("/v1/override")
async def manual_override(request: Request):
    """
    Manual gate override from local operator panel or physical button.

    Body:
    {
        "laneId": "lane-uuid",
        "operatorId": "operator-name"  (optional)
    }
    """
    processor = request.app.state.event_processor
    if not processor:
        return JSONResponse(
            status_code=503,
            content={"error": "Event processor not initialized"},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON payload"},
        )

    lane_id = body.get("laneId")
    if not lane_id:
        return JSONResponse(
            status_code=400,
            content={"error": "laneId is required"},
        )

    result = processor.process_manual_override(
        lane_id=lane_id,
        operator_id=body.get("operatorId"),
    )
    return result


@router.get("/v1/status")
async def get_status(request: Request):
    """
    Device health and sync status for local dashboard / monitoring.
    Returns current heartbeat telemetry and sync state.
    """
    heartbeat = request.app.state.heartbeat_service
    cloud_sync = request.app.state.cloud_sync

    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }

    if heartbeat:
        telemetry = heartbeat.collect_telemetry()
        status["telemetry"] = telemetry

    if cloud_sync:
        status["sync"] = cloud_sync.get_sync_status()

    return status


@router.post("/v1/sync")
async def force_sync(request: Request):
    """Force an immediate configuration sync from cloud."""
    cloud_sync = request.app.state.cloud_sync
    if not cloud_sync:
        return JSONResponse(
            status_code=503,
            content={"error": "Cloud sync not initialized"},
        )

    success = cloud_sync.sync_config_now()
    return {
        "status": "synced" if success else "failed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
