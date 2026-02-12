"""
Database repository for all data access operations
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base,
    Permit,
    GuestPass,
    Lane,
    PlateReading,
    EventQueue,
    DeviceConfig,
    HeartbeatLog,
)

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


class Repository:
    """Centralized database repository"""

    def __init__(self, database_url: str = 'sqlite:///data/edge.db'):
        self.engine = create_engine(database_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        logger.info(f"Database initialized: {database_url}")

    def get_session(self) -> Session:
        """Get database session"""
        return self.SessionLocal()

    # ========== Permits ==========

    def upsert_permit(self, permit_data: dict) -> Permit:
        """Insert or update permit"""
        session = self.get_session()
        try:
            permit = session.query(Permit).filter_by(id=permit_data['id']).first()
            if permit:
                for key, value in permit_data.items():
                    setattr(permit, key, value)
            else:
                permit = Permit(**permit_data)
                session.add(permit)
            session.commit()
            return permit
        finally:
            session.close()

    def find_permit_by_plate(self, plate: str, valid_at: datetime) -> Optional[Permit]:
        """Find valid permit by plate"""
        session = self.get_session()
        try:
            return session.query(Permit).filter(
                and_(
                    Permit.plate == plate,
                    Permit.valid_from <= valid_at,
                    (Permit.valid_to.is_(None) | (Permit.valid_to >= valid_at))
                )
            ).first()
        finally:
            session.close()

    def delete_permits(self, permit_ids: List[str]) -> None:
        """Delete permits by IDs"""
        session = self.get_session()
        try:
            session.query(Permit).filter(Permit.id.in_(permit_ids)).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    # ========== Guest Passes ==========

    def upsert_guest_pass(self, guest_pass_data: dict) -> GuestPass:
        """Insert or update guest pass"""
        session = self.get_session()
        try:
            guest_pass = session.query(GuestPass).filter_by(id=guest_pass_data['id']).first()
            if guest_pass:
                for key, value in guest_pass_data.items():
                    setattr(guest_pass, key, value)
            else:
                guest_pass = GuestPass(**guest_pass_data)
                session.add(guest_pass)
            session.commit()
            return guest_pass
        finally:
            session.close()

    def find_guest_pass_by_plate(self, plate: str, valid_at: datetime) -> Optional[GuestPass]:
        """Find valid guest pass by plate"""
        session = self.get_session()
        try:
            return session.query(GuestPass).filter(
                and_(
                    GuestPass.plate == plate,
                    GuestPass.status == 'ACTIVE',
                    GuestPass.valid_from <= valid_at,
                    GuestPass.valid_to >= valid_at
                )
            ).first()
        finally:
            session.close()

    def increment_guest_pass_entries(self, guest_pass_id: str) -> None:
        """Increment current_entries for guest pass"""
        session = self.get_session()
        try:
            guest_pass = session.query(GuestPass).filter_by(id=guest_pass_id).first()
            if guest_pass:
                guest_pass.current_entries += 1
                session.commit()
        finally:
            session.close()

    def delete_guest_passes(self, guest_pass_ids: List[str]) -> None:
        """Delete guest passes by IDs"""
        session = self.get_session()
        try:
            session.query(GuestPass).filter(GuestPass.id.in_(guest_pass_ids)).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    # ========== Lanes ==========

    def upsert_lane(self, lane_data: dict) -> Lane:
        """Insert or update lane"""
        session = self.get_session()
        try:
            lane = session.query(Lane).filter_by(id=lane_data['id']).first()
            if lane:
                for key, value in lane_data.items():
                    setattr(lane, key, value)
            else:
                lane = Lane(**lane_data)
                session.add(lane)
            session.commit()
            return lane
        finally:
            session.close()

    def get_lane_by_id(self, lane_id: str) -> Optional[Lane]:
        """Get lane by ID"""
        session = self.get_session()
        try:
            return session.query(Lane).filter_by(id=lane_id).first()
        finally:
            session.close()

    def update_lane_last_gate_open(self, lane_id: str, timestamp: datetime) -> None:
        """Update lane last_gate_open timestamp"""
        session = self.get_session()
        try:
            lane = session.query(Lane).filter_by(id=lane_id).first()
            if lane:
                lane.last_gate_open = timestamp
                session.commit()
        finally:
            session.close()

    # ========== Plate Readings ==========

    def add_plate_reading(self, plate: str, lane_id: str, confidence: float, timestamp: datetime) -> PlateReading:
        """Add plate reading for multi-read confirmation"""
        session = self.get_session()
        try:
            reading = PlateReading(
                plate=plate,
                lane_id=lane_id,
                confidence=confidence,
                timestamp=timestamp
            )
            session.add(reading)
            session.commit()
            return reading
        finally:
            session.close()

    def get_recent_plate_readings(self, plate: str, lane_id: str, since: datetime) -> List[PlateReading]:
        """Get recent readings for a plate in a lane"""
        session = self.get_session()
        try:
            return session.query(PlateReading).filter(
                and_(
                    PlateReading.plate == plate,
                    PlateReading.lane_id == lane_id,
                    PlateReading.timestamp >= since,
                    PlateReading.processed == False
                )
            ).all()
        finally:
            session.close()

    def mark_readings_processed(self, plate: str, lane_id: str) -> None:
        """Mark readings as processed"""
        session = self.get_session()
        try:
            session.query(PlateReading).filter(
                and_(
                    PlateReading.plate == plate,
                    PlateReading.lane_id == lane_id,
                    PlateReading.processed == False
                )
            ).update({'processed': True})
            session.commit()
        finally:
            session.close()

    def cleanup_old_readings(self, older_than_seconds: int = 60) -> int:
        """Delete old processed readings"""
        session = self.get_session()
        try:
            cutoff = _utcnow() - timedelta(seconds=older_than_seconds)
            deleted = session.query(PlateReading).filter(PlateReading.timestamp < cutoff).delete()
            session.commit()
            return deleted
        finally:
            session.close()

    # ========== Event Queue ==========

    def add_event(self, event_data: dict) -> EventQueue:
        """Add event to queue"""
        session = self.get_session()
        try:
            event = EventQueue(**event_data)
            session.add(event)
            session.commit()
            return event
        finally:
            session.close()

    def get_unacked_events(self, limit: int = 50) -> List[EventQueue]:
        """Get events that haven't been acknowledged by cloud"""
        session = self.get_session()
        try:
            return session.query(EventQueue).filter(
                EventQueue.cloud_ack == False
            ).limit(limit).all()
        finally:
            session.close()

    def mark_event_acked(self, local_id: str, cloud_id: str) -> None:
        """Mark event as acknowledged by cloud"""
        session = self.get_session()
        try:
            event = session.query(EventQueue).filter_by(local_id=local_id).first()
            if event:
                event.cloud_ack = True
                event.cloud_id = cloud_id
                session.commit()
        finally:
            session.close()

    def increment_event_retry(self, local_id: str) -> None:
        """Increment retry count for a failed event upload"""
        session = self.get_session()
        try:
            event = session.query(EventQueue).filter_by(local_id=local_id).first()
            if event:
                event.retry_count += 1
                session.commit()
        finally:
            session.close()

    def get_queued_events_count(self) -> int:
        """Get count of unacked events"""
        session = self.get_session()
        try:
            return session.query(EventQueue).filter(EventQueue.cloud_ack == False).count()
        finally:
            session.close()

    def cleanup_acked_events(self, keep_hours: int = 48) -> int:
        """Delete acknowledged events older than keep_hours"""
        session = self.get_session()
        try:
            cutoff = _utcnow() - timedelta(hours=keep_hours)
            deleted = session.query(EventQueue).filter(
                EventQueue.cloud_ack == True,
                EventQueue.timestamp < cutoff,
            ).delete()
            session.commit()
            return deleted
        finally:
            session.close()

    # ========== Device Config ==========

    def get_config(self, key: str) -> Optional[str]:
        """Get configuration value"""
        session = self.get_session()
        try:
            config = session.query(DeviceConfig).filter_by(key=key).first()
            return config.value if config else None
        finally:
            session.close()

    def set_config(self, key: str, value: str) -> None:
        """Set configuration value"""
        session = self.get_session()
        try:
            config = session.query(DeviceConfig).filter_by(key=key).first()
            if config:
                config.value = value
                config.updated_at = _utcnow()
            else:
                config = DeviceConfig(key=key, value=value)
                session.add(config)
            session.commit()
        finally:
            session.close()

    # ========== Heartbeat Log ==========

    def add_heartbeat_log(self, heartbeat_data: dict) -> HeartbeatLog:
        """Store heartbeat telemetry snapshot"""
        session = self.get_session()
        try:
            log = HeartbeatLog(**heartbeat_data)
            session.add(log)
            session.commit()
            return log
        finally:
            session.close()

    def mark_heartbeat_acked(self, heartbeat_id: int) -> None:
        """Mark heartbeat as sent to cloud"""
        session = self.get_session()
        try:
            log = session.query(HeartbeatLog).filter_by(id=heartbeat_id).first()
            if log:
                log.cloud_ack = True
                session.commit()
        finally:
            session.close()

    def cleanup_old_heartbeats(self, keep_hours: int = 24) -> int:
        """Delete heartbeat logs older than keep_hours"""
        session = self.get_session()
        try:
            cutoff = _utcnow() - timedelta(hours=keep_hours)
            deleted = session.query(HeartbeatLog).filter(
                HeartbeatLog.timestamp < cutoff,
                HeartbeatLog.cloud_ack == True
            ).delete()
            session.commit()
            return deleted
        finally:
            session.close()

    # ========== Maintenance ==========

    def run_cleanup(self) -> dict:
        """Run all cleanup tasks, return counts of deleted rows"""
        readings = self.cleanup_old_readings()
        events = self.cleanup_acked_events()
        heartbeats = self.cleanup_old_heartbeats()
        return {
            'readings': readings,
            'events': events,
            'heartbeats': heartbeats,
        }
