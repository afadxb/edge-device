"""
Cloud sync service - async background sync between edge device and cloud backend

Responsibilities:
1. Configuration sync (Cloud -> Edge): lanes, permits, guest passes
2. Event upload (Edge -> Cloud): batched plate events with ACK tracking
3. Coordinates with HeartbeatService (separate service)
"""
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from database.repository import Repository

logger = logging.getLogger(__name__)

MAX_EVENT_RETRIES = 10


class CloudSyncService:
    """
    Background service for bidirectional cloud synchronization.
    Never blocks gate operations - all sync is async.
    """

    def __init__(
        self,
        repository: Repository,
        controller_id: str,
        api_key: str,
        api_base_url: str,
        config_interval: int = 300,
        event_interval: int = 10,
        event_batch_size: int = 50,
    ):
        self.repo = repository
        self.controller_id = controller_id
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip('/')
        self.config_interval = config_interval
        self.event_interval = event_interval
        self.event_batch_size = event_batch_size

        self._running = False
        self._config_thread: Optional[threading.Thread] = None
        self._event_thread: Optional[threading.Thread] = None
        self._last_config_sync: Optional[str] = None

    @property
    def _headers(self) -> dict:
        return {'x-edge-device-key': self.api_key}

    def start(self) -> None:
        """Start both sync threads"""
        if self._running:
            return
        self._running = True

        self._config_thread = threading.Thread(target=self._config_sync_loop, daemon=True)
        self._event_thread = threading.Thread(target=self._event_upload_loop, daemon=True)

        self._config_thread.start()
        self._event_thread.start()

        logger.info(
            f"Cloud sync started (config every {self.config_interval}s, "
            f"events every {self.event_interval}s)"
        )

    def stop(self) -> None:
        """Stop sync threads"""
        self._running = False
        if self._config_thread:
            self._config_thread.join(timeout=10)
        if self._event_thread:
            self._event_thread.join(timeout=10)
        logger.info("Cloud sync stopped")

    def sync_config_now(self) -> bool:
        """Force an immediate config sync (called at startup or via API)"""
        return self._sync_configuration()

    def get_sync_status(self) -> dict:
        """Return sync status for the status API endpoint."""
        return {
            "lastConfigSync": self._last_config_sync,
            "running": self._running,
        }

    # ==================== CONFIG SYNC (Cloud -> Edge) ====================

    def _config_sync_loop(self) -> None:
        """Background loop for configuration sync"""
        # Wait for first interval before syncing (startup sync is done by main.py)
        while self._running:
            time.sleep(self.config_interval)
            if not self._running:
                break
            try:
                self._sync_configuration()
            except Exception as e:
                logger.error(f"Config sync cycle failed: {e}")

    def _sync_configuration(self) -> bool:
        """Download configuration from cloud and update local database"""
        url = f"{self.api_base_url}/edge-devices/{self.controller_id}/config"
        params = {}
        if self._last_config_sync:
            params['lastSync'] = self._last_config_sync

        try:
            response = requests.get(url, headers=self._headers, params=params, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Config sync failed: HTTP {response.status_code}")
                return False

            config = response.json()
            self._apply_configuration(config)
            self._last_config_sync = config.get('timestamp')
            logger.info(f"Config synced: {len(config.get('lanes', []))} lanes, "
                        f"{len(config.get('permits', []))} permits, "
                        f"{len(config.get('guestPasses', []))} guest passes")
            return True

        except requests.RequestException as e:
            logger.warning(f"Config sync failed (cloud unreachable): {e}")
            return False

    def _apply_configuration(self, config: dict) -> None:
        """Apply downloaded configuration to local database"""
        now = datetime.now(timezone.utc)

        # Upsert lanes
        for lane_data in config.get('lanes', []):
            self.repo.upsert_lane({
                'id': lane_data['id'],
                'name': lane_data['name'],
                'direction': lane_data['direction'],
                'settings': lane_data.get('settings'),
                'synced_at': now,
            })

        # Upsert permits (vehicles -> local permits)
        for permit_data in config.get('permits', []):
            self.repo.upsert_permit({
                'id': permit_data['id'],
                'plate': permit_data['plate'],
                'type': permit_data.get('type', 'RESIDENT'),
                'valid_from': datetime.fromisoformat(permit_data['validFrom'].replace('Z', '+00:00')) if permit_data.get('validFrom') else now,
                'valid_to': datetime.fromisoformat(permit_data['validTo'].replace('Z', '+00:00')) if permit_data.get('validTo') else None,
                'metadata': permit_data.get('metadata'),
                'synced_at': now,
            })

        # Upsert guest passes
        for gp_data in config.get('guestPasses', []):
            self.repo.upsert_guest_pass({
                'id': gp_data['id'],
                'plate': gp_data['plate'],
                'valid_from': datetime.fromisoformat(gp_data['validFrom'].replace('Z', '+00:00')),
                'valid_to': datetime.fromisoformat(gp_data['validTo'].replace('Z', '+00:00')),
                'max_entries': gp_data.get('maxEntries'),
                'current_entries': gp_data.get('currentEntries', 0),
                'status': gp_data.get('status', 'ACTIVE'),
                'metadata': gp_data.get('metadata'),
                'synced_at': now,
            })

        # Delete removed permits
        deleted_permits = config.get('deletedPermitIds', [])
        if deleted_permits:
            self.repo.delete_permits(deleted_permits)
            logger.info(f"Deleted {len(deleted_permits)} revoked permits")

        # Delete removed guest passes
        deleted_passes = config.get('deletedGuestPassIds', [])
        if deleted_passes:
            self.repo.delete_guest_passes(deleted_passes)
            logger.info(f"Deleted {len(deleted_passes)} revoked guest passes")

    # ==================== EVENT UPLOAD (Edge -> Cloud) ====================

    def _event_upload_loop(self) -> None:
        """Background loop for event uploads"""
        while self._running:
            time.sleep(self.event_interval)
            if not self._running:
                break
            try:
                self._upload_events()
            except Exception as e:
                logger.error(f"Event upload cycle failed: {e}")

    def _upload_events(self) -> bool:
        """Upload unacknowledged events to cloud"""
        events = self.repo.get_unacked_events(limit=self.event_batch_size)
        if not events:
            return True

        # Filter out events that exceeded max retries
        uploadable = []
        for event in events:
            if event.retry_count >= MAX_EVENT_RETRIES:
                logger.warning(f"Event {event.local_id} exceeded max retries ({MAX_EVENT_RETRIES}), marking as dead letter")
                self.repo.mark_event_acked(event.local_id, 'DEAD_LETTER')
                continue
            uploadable.append(event)

        if not uploadable:
            return True

        # Build batch payload
        now_iso = datetime.now(timezone.utc).isoformat()
        batch = []
        for event in uploadable:
            event_data = {
                'localId': event.local_id,
                'laneId': event.lane_id,
                'plate': event.plate,
                'confidence': event.confidence,
                'decision': event.decision,
                'reasonCode': event.reason_code or '',
                'matchedEntityId': event.matched_entity_id,
                'matchedEntityType': event.matched_entity_type,
                'gateOpened': event.gate_opened,
                'timestamp': event.timestamp.isoformat() + 'Z' if event.timestamp else now_iso,
            }

            # Add optional data from event.data JSON
            if event.data:
                if event.data.get('processing_time'):
                    event_data['processingTime'] = event.data['processing_time']

            batch.append(event_data)

        url = f"{self.api_base_url}/edge-devices/{self.controller_id}/events"

        try:
            response = requests.post(
                url,
                json={'events': batch},
                headers=self._headers,
                timeout=30,
            )

            if response.status_code in (200, 201):
                result = response.json()
                synced_ids = result.get('syncedEventIds', {})

                # Mark events as acknowledged
                for local_id, cloud_id in synced_ids.items():
                    self.repo.mark_event_acked(local_id, cloud_id)

                processed = result.get('processed', 0)
                errors = result.get('errors', [])

                if errors:
                    logger.warning(f"Event upload partial: {processed} ok, {len(errors)} errors")
                else:
                    logger.debug(f"Events uploaded: {processed}")

                return True
            else:
                # Increment retry count for all events in this batch
                for event in uploadable:
                    self.repo.increment_event_retry(event.local_id)
                logger.warning(f"Event upload failed: HTTP {response.status_code}")
                return False

        except requests.RequestException as e:
            # Increment retry count on network failure
            for event in uploadable:
                self.repo.increment_event_retry(event.local_id)
            logger.warning(f"Event upload failed (cloud unreachable): {e}")
            return False
