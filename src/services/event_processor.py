"""
Event processor - orchestrates the plate detection pipeline

Flow:
1. Receive webhook from Plate Recognizer Stream
2. Run access control decision engine
3. Trigger GPIO relay if access granted
4. Queue event for cloud sync
5. Update heartbeat telemetry timestamps
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from database.repository import Repository
from database.models import Lane
from hardware.base import HardwareInterface
from services.access_control import (
    AccessDecisionEngine,
    ACCESS_GRANTED,
    MANUAL_OVERRIDE,
    EXIT_LOG,
    SYSTEM_FAULT,
)

logger = logging.getLogger(__name__)

# Map decision codes to entity types for cloud sync
_ENTITY_TYPE_MAP = {
    'PERMIT_RESIDENT': 'RESIDENT',
    'PERMIT_STAFF': 'STAFF',
    'PERMIT_VIP': 'VIP',
    'GUEST_PASS': 'VISITOR',
    'OPERATOR_OVERRIDE': 'OPERATOR',
}


def _utcnow():
    return datetime.now(timezone.utc)


class EventProcessor:
    """
    Processes plate detection webhooks from Plate Recognizer Stream.
    Coordinates access control, gate control, and event queuing.
    """

    def __init__(
        self,
        repository: Repository,
        hardware: Optional[HardwareInterface],
        access_engine: AccessDecisionEngine,
        heartbeat_service=None,
    ):
        self.repo = repository
        self.hardware = hardware
        self.engine = access_engine
        self.heartbeat = heartbeat_service

    def process_stream_webhook(self, payload: dict) -> dict:
        """
        Process a webhook payload from Plate Recognizer Stream (on-premise).

        Returns:
            dict with processing result
        """
        start_time = time.monotonic()

        try:
            # Parse Stream on-premise webhook format
            hook = payload.get('hook', {})
            data = payload.get('data', {})

            # Only process recognition events (ignore video_file, etc.)
            event_type = hook.get('event', 'recognition')
            if event_type != 'recognition':
                return {'status': 'skipped', 'reason': f'non_recognition_event:{event_type}'}

            results = data.get('results', [])

            if not results:
                return {'status': 'skipped', 'reason': 'no_plates'}

            # Process the best result (highest confidence)
            best = max(results, key=lambda r: r.get('score', 0))
            plate = best.get('plate', '').upper().strip()
            confidence = best.get('score', 0.0)
            # camera_id from data takes priority, fall back to hook.id
            camera_id = data.get('camera_id') or hook.get('id', 'unknown')

            if not plate:
                return {'status': 'skipped', 'reason': 'empty_plate'}

            # Notify heartbeat of plate detection
            if self.heartbeat:
                self.heartbeat.record_plate_seen()
                self.heartbeat.record_webhook_received()

            # Determine lane for this camera
            lane_id = self._resolve_lane_id(camera_id)

            if not lane_id:
                logger.warning(f"No lane mapped for camera {camera_id}, using default")
                lane_id = self._get_default_lane_id()

            if not lane_id:
                logger.error("No lanes configured - cannot process plate")
                return {'status': 'error', 'reason': 'no_lanes'}

            # Get lane direction
            lane = self.repo.get_lane_by_id(lane_id)
            if not lane:
                logger.error(f"Lane {lane_id} not found")
                return {'status': 'error', 'reason': 'lane_not_found'}

            direction = lane.direction

            # Run access control
            if direction in ('IN', 'BOTH'):
                decision, reason_code, matched_id, should_open = self.engine.evaluate_entry_lane(
                    plate, confidence, lane_id
                )
            else:
                decision, reason_code, matched_id, should_open = self.engine.evaluate_exit_lane(
                    plate, confidence, lane_id
                )

            # Trigger gate if access granted
            gate_opened = False
            if should_open:
                gate_opened = self._trigger_gate(lane_id)

            processing_time = int((time.monotonic() - start_time) * 1000)

            # Derive matched_entity_type from reason_code
            matched_entity_type = _ENTITY_TYPE_MAP.get(reason_code)

            # Queue event for cloud sync (always, regardless of decision)
            local_id = str(uuid.uuid4())
            self.repo.add_event({
                'local_id': local_id,
                'lane_id': lane_id,
                'plate': plate,
                'confidence': confidence,
                'decision': decision,
                'reason_code': reason_code,
                'matched_entity_id': matched_id,
                'matched_entity_type': matched_entity_type,
                'gate_opened': gate_opened,
                'timestamp': _utcnow(),
                'data': {
                    'camera_id': camera_id,
                    'processing_time': processing_time,
                    'direction': direction,
                    'bounding_box': best.get('box'),
                    'region': best.get('region', {}).get('code'),
                    'vehicle_type': best.get('vehicle', {}).get('type'),
                    'model_make': best.get('model_make', [{}])[0].get('make') if best.get('model_make') else None,
                    'vehicle_model': best.get('model_make', [{}])[0].get('model') if best.get('model_make') else None,
                    'orientation': best.get('orientation', [{}])[0].get('orientation') if best.get('orientation') else None,
                    'travel_direction': best.get('direction'),
                    'speed': best.get('speed'),
                    'timestamp_camera': data.get('timestamp_camera'),
                },
            })

            logger.info(
                f"Processed: {plate} | {decision} | {reason_code} | "
                f"gate={'OPENED' if gate_opened else 'closed'} | {processing_time}ms"
            )

            return {
                'status': 'processed',
                'plate': plate,
                'decision': decision,
                'reason_code': reason_code,
                'gate_opened': gate_opened,
                'processing_time': processing_time,
            }

        except Exception as e:
            logger.error(f"Event processing failed: {e}", exc_info=True)
            return {'status': 'error', 'reason': str(e)}

    def process_manual_override(self, lane_id: str, operator_id: str = None) -> dict:
        """Process a manual gate override from the local API"""
        decision, reason_code, matched_id, should_open = self.engine.manual_override(
            lane_id, operator_id
        )

        gate_opened = False
        if should_open:
            gate_opened = self._trigger_gate(lane_id)

        # Queue event
        local_id = str(uuid.uuid4())
        self.repo.add_event({
            'local_id': local_id,
            'lane_id': lane_id,
            'plate': 'MANUAL',
            'confidence': 1.0,
            'decision': decision,
            'reason_code': reason_code,
            'matched_entity_id': matched_id,
            'matched_entity_type': 'OPERATOR',
            'gate_opened': gate_opened,
            'timestamp': _utcnow(),
            'data': {
                'operator_id': operator_id,
                'direction': 'IN',
            },
        })

        return {
            'status': 'processed',
            'decision': decision,
            'gate_opened': gate_opened,
        }

    def _trigger_gate(self, lane_id: str) -> bool:
        """Trigger the GPIO relay and update lane cooldown"""
        if not self.hardware:
            logger.warning("No hardware interface - gate trigger skipped")
            return False
        try:
            success = self.hardware.trigger_relay()
            if success:
                self.repo.update_lane_last_gate_open(lane_id, _utcnow())
                if self.heartbeat:
                    self.heartbeat.record_gate_trigger()
                logger.info(f"Gate opened for lane {lane_id}")
            return success
        except Exception as e:
            logger.error(f"Gate trigger failed: {e}")
            return False

    def _resolve_lane_id(self, camera_id: str) -> Optional[str]:
        """Resolve camera_id to lane_id from device config"""
        mapping = self.repo.get_config('camera_lane_mapping')
        if mapping:
            try:
                camera_map = json.loads(mapping)
                return camera_map.get(camera_id)
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _get_default_lane_id(self) -> Optional[str]:
        """Get the first available lane as fallback"""
        session = self.repo.get_session()
        try:
            lane = session.query(Lane).first()
            return lane.id if lane else None
        finally:
            session.close()
