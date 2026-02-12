"""
SQLite database models for edge device persistent storage

Event Decision Taxonomy:
- ACCESS_GRANTED: Vehicle permitted, gate opened
- ACCESS_DENIED_CONFIDENCE: Denied due to low OCR confidence
- ACCESS_DENIED_NO_PERMIT: Denied due to no valid permit/guest pass
- TAILGATE_BLOCKED: Denied due to cooldown or insufficient multi-reads
- MANUAL_OVERRIDE: Gate manually opened by operator
- SYSTEM_FAULT: Hardware/system error prevented decision
- EXIT_LOG: Exit lane logging (no gate control)
"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, JSON, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utcnow():
    return datetime.now(timezone.utc)


class Permit(Base):
    """Permanent or long-term permits (residents, staff, VIP)"""
    __tablename__ = 'permits'

    id = Column(String, primary_key=True)
    plate = Column(String, index=True, nullable=False)
    type = Column(String)  # RESIDENT, STAFF, VIP
    valid_from = Column(DateTime)
    valid_to = Column(DateTime, nullable=True)  # null = permanent
    metadata = Column(JSON)
    synced_at = Column(DateTime, default=_utcnow)


class GuestPass(Base):
    """Time-bounded guest passes"""
    __tablename__ = 'guest_passes'

    id = Column(String, primary_key=True)
    plate = Column(String, index=True, nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    max_entries = Column(Integer, nullable=True)
    current_entries = Column(Integer, default=0)
    status = Column(String)  # ACTIVE, EXPIRED, REVOKED
    metadata = Column(JSON)
    synced_at = Column(DateTime, default=_utcnow)


class Lane(Base):
    """Lane configuration (synced from cloud)"""
    __tablename__ = 'lanes'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    direction = Column(String)  # IN (entry with gate), OUT (exit log-only), BOTH
    settings = Column(JSON)  # cooldown, gate_duration, multi_read_count, etc.
    last_gate_open = Column(DateTime, nullable=True)  # for cooldown tracking
    synced_at = Column(DateTime, default=_utcnow)


class PlateReading(Base):
    """Temporary storage for multi-read confirmation"""
    __tablename__ = 'plate_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    plate = Column(String, index=True, nullable=False)
    lane_id = Column(String, index=True, nullable=False)
    confidence = Column(Float)
    timestamp = Column(DateTime, default=_utcnow, index=True)
    processed = Column(Boolean, default=False)


class EventQueue(Base):
    """Events waiting for cloud ACK (NEVER deleted until ACK received)

    Decision field uses standardized taxonomy:
    ACCESS_GRANTED, ACCESS_DENIED_CONFIDENCE, ACCESS_DENIED_NO_PERMIT,
    TAILGATE_BLOCKED, MANUAL_OVERRIDE, SYSTEM_FAULT, EXIT_LOG
    """
    __tablename__ = 'event_queue'

    id = Column(Integer, primary_key=True, autoincrement=True)
    local_id = Column(String, unique=True, nullable=False, index=True)
    lane_id = Column(String, index=True)
    plate = Column(String, index=True)
    confidence = Column(Float)
    decision = Column(String)  # Standardized taxonomy (see docstring)
    reason_code = Column(String)  # Detail: PERMIT_RESIDENT, UNKNOWN_PLATE, LOW_CONFIDENCE, etc.
    matched_entity_id = Column(String, nullable=True)
    matched_entity_type = Column(String, nullable=True)  # RESIDENT, VISITOR, WATCHLIST
    gate_opened = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=_utcnow, index=True)
    cloud_ack = Column(Boolean, default=False, index=True)  # ACK from cloud
    cloud_id = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)
    data = Column(JSON)  # Full event data


class DeviceConfig(Base):
    """Device configuration key-value store"""
    __tablename__ = 'config'

    key = Column(String, primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=_utcnow)


class HeartbeatLog(Base):
    """Local heartbeat telemetry history for diagnostics"""
    __tablename__ = 'heartbeat_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=_utcnow, index=True)
    status = Column(String)  # OK, WARN, ERROR
    uptime = Column(Integer)  # seconds
    memory_usage = Column(Float)  # percentage
    cpu_temp = Column(Float)  # Celsius
    disk_space_mb = Column(Integer)  # available MB
    gpio_health = Column(String)  # OK, FAULT, UNKNOWN
    stream_health = Column(String)  # OK, FAULT, UNKNOWN
    sqlite_queue_depth = Column(Integer)  # unacked events count
    last_plate_seen = Column(DateTime, nullable=True)
    last_gate_trigger = Column(DateTime, nullable=True)
    cloud_ack = Column(Boolean, default=False)  # Whether this heartbeat was sent to cloud
    errors = Column(JSON, nullable=True)  # Array of error strings
