"""
Heartbeat service for edge device health reporting

Collects enriched telemetry and sends periodic heartbeats to the cloud backend:
- System metrics: uptime, memory, CPU temp, disk space
- Hardware health: GPIO relay status, Plate Recognizer Stream webhook health
- Operational data: last plate seen, last gate trigger, SQLite queue depth
"""
import logging
import os
import time
import shutil
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from database.repository import Repository
from hardware.base import HardwareInterface

logger = logging.getLogger(__name__)


class HeartbeatService:
    """
    Collects enriched device telemetry and sends heartbeats to cloud backend.
    Runs on a configurable interval (default 60s from registration response).
    """

    def __init__(
        self,
        repository: Repository,
        hardware: Optional[HardwareInterface],
        controller_id: str,
        api_key: str,
        api_base_url: str,
        interval: int = 60,
        db_path: str = 'data/edge.db',
        stream_health_timeout: int = 120,
    ):
        self.repo = repository
        self.hardware = hardware
        self.controller_id = controller_id
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip('/')
        self.interval = interval
        self.db_path = db_path

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time = time.monotonic()

        # Track operational timestamps (updated externally)
        self.last_plate_seen: Optional[datetime] = None
        self.last_gate_trigger: Optional[datetime] = None

        # Stream webhook health tracking
        self._last_webhook_received: Optional[float] = None
        self._webhook_timeout = stream_health_timeout

    def start(self) -> None:
        """Start the heartbeat background thread"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"Heartbeat service started (interval={self.interval}s)")

    def stop(self) -> None:
        """Stop the heartbeat background thread"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Heartbeat service stopped")

    def record_plate_seen(self, timestamp: Optional[datetime] = None) -> None:
        """Call when a plate is detected to update last_plate_seen"""
        self.last_plate_seen = timestamp or datetime.now(timezone.utc)

    def record_gate_trigger(self, timestamp: Optional[datetime] = None) -> None:
        """Call when a gate is triggered to update last_gate_trigger"""
        self.last_gate_trigger = timestamp or datetime.now(timezone.utc)

    def record_webhook_received(self) -> None:
        """Call when a Plate Recognizer Stream webhook is received"""
        self._last_webhook_received = time.monotonic()

    def _run_loop(self) -> None:
        """Main heartbeat loop"""
        while self._running:
            try:
                telemetry = self.collect_telemetry()
                self._send_heartbeat(telemetry)
            except Exception as e:
                logger.error(f"Heartbeat cycle failed: {e}")
            time.sleep(self.interval)

    def collect_telemetry(self) -> dict:
        """Collect all enriched telemetry data"""
        uptime = int(time.monotonic() - self._start_time)

        # System metrics
        memory_usage = self._get_memory_usage()
        cpu_temp = self._get_cpu_temp()
        disk_space_mb = self._get_disk_space_mb()

        # Hardware health
        gpio_health = self._check_gpio_health()
        stream_health = self._check_stream_health()

        # Queue depth
        sqlite_queue_depth = self.repo.get_queued_events_count()

        # Determine overall status
        status = self._determine_status(
            gpio_health=gpio_health,
            stream_health=stream_health,
            disk_space_mb=disk_space_mb,
            cpu_temp=cpu_temp,
        )

        # Collect errors
        errors = self._collect_errors(
            gpio_health=gpio_health,
            stream_health=stream_health,
            disk_space_mb=disk_space_mb,
        )

        telemetry = {
            'status': status,
            'uptime': uptime,
            'memoryUsage': memory_usage,
            'cpuTemp': cpu_temp,
            'diskSpaceMb': disk_space_mb,
            'gpioHealth': gpio_health,
            'streamHealth': stream_health,
            'sqliteQueueDepth': sqlite_queue_depth,
        }

        if self.last_plate_seen:
            telemetry['lastPlateSeen'] = self.last_plate_seen.isoformat() + 'Z'
        if self.last_gate_trigger:
            telemetry['lastGateTrigger'] = self.last_gate_trigger.isoformat() + 'Z'
        if errors:
            telemetry['errors'] = errors

        # Store locally for diagnostics
        self.repo.add_heartbeat_log({
            'status': status,
            'uptime': uptime,
            'memory_usage': memory_usage,
            'cpu_temp': cpu_temp,
            'disk_space_mb': disk_space_mb,
            'gpio_health': gpio_health,
            'stream_health': stream_health,
            'sqlite_queue_depth': sqlite_queue_depth,
            'last_plate_seen': self.last_plate_seen,
            'last_gate_trigger': self.last_gate_trigger,
            'errors': errors if errors else None,
        })

        return telemetry

    def _send_heartbeat(self, telemetry: dict) -> bool:
        """Send heartbeat to cloud backend"""
        url = f"{self.api_base_url}/edge-devices/{self.controller_id}/heartbeat"
        headers = {'x-edge-device-key': self.api_key}

        try:
            response = requests.post(url, json=telemetry, headers=headers, timeout=10)
            if response.status_code == 200 or response.status_code == 201:
                logger.debug(f"Heartbeat sent: status={telemetry['status']}")
                return True
            else:
                logger.warning(f"Heartbeat rejected: HTTP {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.warning(f"Heartbeat send failed (cloud unreachable): {e}")
            return False

    def _get_memory_usage(self) -> Optional[float]:
        """Get memory usage percentage"""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = None
            mem_available = None
            for line in lines:
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1])
            if mem_total and mem_available:
                return round((1 - mem_available / mem_total) * 100, 1)
        except Exception:
            pass
        return None

    def _get_cpu_temp(self) -> Optional[float]:
        """Get CPU temperature using hardware interface"""
        try:
            if self.hardware:
                return self.hardware.get_cpu_temp()
        except Exception:
            pass
        return None

    def _get_disk_space_mb(self) -> Optional[int]:
        """Get available disk space in MB for the database partition"""
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            usage = shutil.disk_usage(db_dir)
            return int(usage.free / (1024 * 1024))
        except Exception:
            return None

    def _check_gpio_health(self) -> str:
        """Check GPIO relay health status"""
        try:
            if not self.hardware or not self.hardware.is_initialized():
                return 'UNKNOWN'
            return 'OK'
        except Exception:
            return 'FAULT'

    def _check_stream_health(self) -> str:
        """Check Plate Recognizer Stream webhook health"""
        if self._last_webhook_received is None:
            return 'UNKNOWN'
        elapsed = time.monotonic() - self._last_webhook_received
        if elapsed > self._webhook_timeout:
            return 'FAULT'
        return 'OK'

    def _determine_status(
        self,
        gpio_health: str,
        stream_health: str,
        disk_space_mb: Optional[int],
        cpu_temp: Optional[float],
    ) -> str:
        """Determine overall device status from component health"""
        # ERROR: critical hardware failure
        if gpio_health == 'FAULT':
            return 'ERROR'

        # WARN conditions
        warnings = []
        if stream_health == 'FAULT':
            warnings.append('stream_down')
        if disk_space_mb is not None and disk_space_mb < 500:
            warnings.append('low_disk')
        if cpu_temp is not None and cpu_temp > 80:
            warnings.append('high_temp')

        if warnings:
            return 'WARN'

        return 'OK'

    def _collect_errors(
        self,
        gpio_health: str,
        stream_health: str,
        disk_space_mb: Optional[int],
    ) -> list:
        """Collect error messages for the heartbeat payload"""
        errors = []
        if gpio_health == 'FAULT':
            errors.append('GPIO relay not responding')
        if stream_health == 'FAULT':
            errors.append('Plate Recognizer Stream webhook not received recently')
        if disk_space_mb is not None and disk_space_mb < 200:
            errors.append(f'Critical disk space: {disk_space_mb}MB remaining')
        return errors
