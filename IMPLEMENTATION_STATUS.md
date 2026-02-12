# Edge Device Implementation Status

## ✅ Completed (Backend)

- Database schema with edge device fields
- EdgeDevicesModule with full API
- Registration endpoint + API key generation
- Configuration sync endpoint (Cloud → Edge)
- Event upload endpoint (Edge → Cloud)
- Heartbeat endpoint with enriched telemetry
- Authentication guard (x-edge-device-key)
- Integration with AppModule

## ✅ Completed (Edge Device)

### Hardware Layer
- ✅ Abstract base class (src/hardware/base.py)
- ✅ NVIDIA Jetson Nano implementation (src/hardware/jetson_nano.py)
- ✅ Platform detection factory (src/hardware/factory.py)
- ✅ Non-blocking GPIO relay trigger (threaded pulse via Jetson.GPIO)

### Database Layer
- ✅ SQLite models (src/database/models.py)
  - Permit, GuestPass, Lane, PlateReading, EventQueue, DeviceConfig, HeartbeatLog
  - Uses DeclarativeBase (modern SQLAlchemy)
  - Timezone-aware UTC timestamps
- ✅ Repository with all CRUD operations (src/database/repository.py)
  - Event retry tracking with increment_event_retry()
  - DB cleanup (acked events, stale readings)
  - expire_on_commit=False to prevent DetachedInstanceError

### Access Control
- ✅ Entry lane logic - 4 conditions (src/services/access_control.py)
  - Confidence threshold, multi-read confirmation, permit/guest pass check, cooldown
- ✅ Exit lane logic - log only
- ✅ Manual override support
- ✅ Standardized decision taxonomy (ACCESS_GRANTED, ACCESS_DENIED_*, TAILGATE_BLOCKED, etc.)

### Core Services
- ✅ Cloud sync service (src/services/cloud_sync.py)
  - Config sync (Cloud → Edge): lanes, permits, guest passes
  - Event upload (Edge → Cloud): batched with ACK tracking
  - Retry logic with MAX_EVENT_RETRIES=10 and dead letter handling
  - Public get_sync_status() API
- ✅ Event processor (src/services/event_processor.py)
  - Plate Recognizer Stream webhook processing
  - Access control orchestration
  - Entity type mapping (reason_code → entity type)
  - Manual override support
- ✅ Heartbeat service (src/services/heartbeat.py)
  - Enriched telemetry: uptime, memory, CPU temp, disk, GPIO health, stream health
  - Configurable stream_health_timeout
  - Local heartbeat log storage
  - Null-safe hardware access

### API Layer
- ✅ FastAPI application (src/api/app.py)
  - Lifespan context manager (modern FastAPI)
  - Docs disabled in production (EDGE_ENABLE_DOCS env var)
- ✅ Webhook routes (src/api/routes.py)
  - POST /v1/webhook/stream - Plate Recognizer webhook
  - POST /v1/manual-override - Manual gate override
  - GET /v1/status - Device status
  - POST /v1/sync - Force config sync
  - GET /healthz - Docker health check

### Configuration
- ✅ Settings loader (src/config/settings.py)
  - YAML config with environment variable overrides
- ✅ Config template (config/config.yaml.template)
- ✅ Stream config template (config/stream-config.ini.template)

### Main Application
- ✅ Main entry point (src/main.py)
  - Service wiring and startup
  - Graceful shutdown (SIGINT/SIGTERM)
  - Periodic DB cleanup (hourly)
  - Initial config sync on startup

### Deployment
- ✅ Installation script (scripts/install.sh)
  - Platform detection (NVIDIA Jetson Nano)
  - Python venv, GPIO libs, systemd service
- ✅ Systemd service (systemd/edge-device.service)
- ✅ Device registration script (scripts/register_device.py)
- ✅ Dockerfile with healthcheck
- ✅ Docker Compose (Plate Recognizer Jetson Stream + edge device)
- ✅ .env.example (with JETPACK_TAG for JetPack version selection)

## Testing Checklist

### Backend Testing
- [ ] Device registration returns API key
- [ ] Config sync returns lanes/permits/guest passes
- [ ] Event upload accepts batch
- [ ] API key authentication works

### Edge Device Testing
- [ ] Hardware detection works (Jetson Nano)
- [ ] GPIO relay triggers correctly
- [ ] SQLite database initializes
- [ ] Access control logic works
- [ ] Multi-read confirmation works
- [ ] Cooldown prevents rapid cycling

### Integration Testing
- [ ] Plate Recognizer webhook received
- [ ] Entry lane opens gate on valid permit
- [ ] Exit lane logs only
- [ ] Events sync to cloud
- [ ] Config downloads from cloud
- [ ] Heartbeat updates status

## Deployment Guide

### Backend Deployment
```bash
cd backend
npx prisma generate
npm run build
pm2 restart anpr-backend
```

### Edge Device Deployment
```bash
# On NVIDIA Jetson Nano
cd /opt
git clone <repo> edge-device
cd edge-device
sudo ./scripts/install.sh

# Edit configuration
sudo nano /opt/edge-device/config/config.yaml

# Register device
sudo /opt/edge-device/venv/bin/python /opt/edge-device/scripts/register_device.py \
  --api-url https://api.example.com

# Start services
docker-compose up -d  # Plate Recognizer Stream
sudo systemctl start edge-device
sudo systemctl enable edge-device
```

## Architecture Summary

```
Plate Recognizer Stream (Docker, GPU-accelerated on Jetson)
  ↓ webhook (http://localhost:8001/v1/webhook/stream)
Edge Device (Python/FastAPI on Jetson Nano)
  - Receive plate detection
  - Multi-read confirmation (anti-tailgate)
  - Check local permit/guest pass (SQLite)
  - Fire GPIO relay if allowed
  - Queue event for cloud sync
  ↕ async background sync
Cloud Backend (NestJS)
  - Manage permits/guest passes
  - Receive events (batched with ACK)
  - Push config updates (lanes, permits)
  - Receive heartbeat telemetry
```

## Key Design Principles

1. **Edge Autonomy**: All decisions made locally, never wait for cloud
2. **Persistent Storage**: Events stored until cloud ACK received
3. **Async Sync**: Background sync threads, never block gate operations
4. **Lane Types**: Entry (with gate control) vs Exit (log only)
5. **Multi-Read**: Anti-tailgating via configurable read count within time window
6. **Cooldown**: Prevents rapid gate cycling per lane
7. **Dead Letter**: Events exceeding max retries are marked as dead letters
8. **Graceful Degradation**: Works without hardware, cloud, or Stream
