"""
Access control decision engine for ENTRY and EXIT lanes

Standardized Event Taxonomy:
- ACCESS_GRANTED: Vehicle permitted, gate opened
- ACCESS_DENIED_CONFIDENCE: Denied due to low OCR confidence
- ACCESS_DENIED_NO_PERMIT: Denied due to no valid permit/guest pass
- TAILGATE_BLOCKED: Denied due to cooldown or insufficient multi-reads
- MANUAL_OVERRIDE: Gate manually opened by operator
- SYSTEM_FAULT: Hardware/system error prevented decision
- EXIT_LOG: Exit lane logging (no gate control)

ENTRY Lane Logic:
- Gate opens ONLY IF ALL conditions pass
- Conditions: confidence, multi-read, permit/guest pass, cooldown

EXIT Lane Logic:
- Just log, no gate control
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional

from database.repository import Repository

logger = logging.getLogger(__name__)

# Standardized event decision taxonomy
ACCESS_GRANTED = 'ACCESS_GRANTED'
ACCESS_DENIED_CONFIDENCE = 'ACCESS_DENIED_CONFIDENCE'
ACCESS_DENIED_NO_PERMIT = 'ACCESS_DENIED_NO_PERMIT'
TAILGATE_BLOCKED = 'TAILGATE_BLOCKED'
MANUAL_OVERRIDE = 'MANUAL_OVERRIDE'
SYSTEM_FAULT = 'SYSTEM_FAULT'
EXIT_LOG = 'EXIT_LOG'


class AccessDecisionEngine:
    """
    Access control decision engine using standardized event taxonomy.
    Entry Lane: All conditions must pass for gate to open
    Exit Lane: Just log, no decision engine
    """

    def __init__(self, repository: Repository):
        self.repo = repository

    def evaluate_entry_lane(
        self,
        plate: str,
        confidence: float,
        lane_id: str
    ) -> Tuple[str, str, Optional[str], bool]:
        """
        ENTRY LANE: All conditions must pass for gate to open

        Args:
            plate: License plate number
            confidence: OCR confidence score (0.0 - 1.0)
            lane_id: Lane identifier

        Returns:
            Tuple of (decision, reason_code, matched_entity_id, should_open_gate)
            - decision: Standardized taxonomy value
            - reason_code: Specific detail reason
            - matched_entity_id: Permit/guest pass ID if matched
            - should_open_gate: True only if ALL conditions pass
        """
        now = datetime.now(timezone.utc)

        try:
            lane = self.repo.get_lane_by_id(lane_id)
        except Exception as e:
            logger.error(f"Failed to fetch lane {lane_id}: {e}")
            return (SYSTEM_FAULT, 'DATABASE_ERROR', None, False)

        if not lane:
            logger.error(f"Lane {lane_id} not found in database")
            return (SYSTEM_FAULT, 'LANE_NOT_FOUND', None, False)

        settings = lane.settings or {}

        # Get thresholds from lane settings
        min_confidence = settings.get('min_confidence', 0.80)
        multi_read_count = settings.get('multi_read_count', 2)
        multi_read_window = settings.get('multi_read_window', 5)
        cooldown_seconds = settings.get('cooldown', 3)

        # ========== CONDITION 1: Confidence Threshold ==========
        if confidence < min_confidence:
            logger.info(
                f"ACCESS_DENIED_CONFIDENCE - {confidence:.2f} < {min_confidence} "
                f"[{plate}]"
            )
            return (ACCESS_DENIED_CONFIDENCE, 'LOW_CONFIDENCE', None, False)

        # ========== CONDITION 2: Multi-Read Confirmation ==========
        since = now - timedelta(seconds=multi_read_window)
        recent_reads = self.repo.get_recent_plate_readings(plate, lane_id, since)

        # Add current reading
        self.repo.add_plate_reading(plate, lane_id, confidence, now)

        if len(recent_reads) + 1 < multi_read_count:
            logger.info(
                f"TAILGATE_BLOCKED - Insufficient reads: {len(recent_reads) + 1}/{multi_read_count} "
                f"[{plate}]"
            )
            return (TAILGATE_BLOCKED, 'INSUFFICIENT_READS', None, False)

        # Mark readings as processed
        self.repo.mark_readings_processed(plate, lane_id)

        # ========== CONDITION 3: Check Permit or Guest Pass ==========
        permit = self.repo.find_permit_by_plate(plate, now)
        guest_pass = self.repo.find_guest_pass_by_plate(plate, now)

        matched_entity_id = None
        reason_code = None

        if permit:
            matched_entity_id = permit.id
            reason_code = f'PERMIT_{permit.type}'
            logger.info(f"Permit matched: {reason_code} [{plate}]")

        elif guest_pass:
            if guest_pass.status != 'ACTIVE':
                logger.info(f"ACCESS_DENIED_NO_PERMIT - Guest pass inactive [{plate}]")
                return (ACCESS_DENIED_NO_PERMIT, 'GUEST_PASS_INACTIVE', guest_pass.id, False)

            if guest_pass.max_entries and guest_pass.current_entries >= guest_pass.max_entries:
                logger.info(
                    f"ACCESS_DENIED_NO_PERMIT - Guest pass max entries reached: "
                    f"{guest_pass.current_entries}/{guest_pass.max_entries} [{plate}]"
                )
                return (ACCESS_DENIED_NO_PERMIT, 'GUEST_PASS_MAX_ENTRIES', guest_pass.id, False)

            matched_entity_id = guest_pass.id
            reason_code = 'GUEST_PASS'
            logger.info(f"Guest pass matched [{plate}]")

            # Increment entry count
            self.repo.increment_guest_pass_entries(guest_pass.id)

        else:
            logger.info(f"ACCESS_DENIED_NO_PERMIT - Unknown plate: {plate}")
            return (ACCESS_DENIED_NO_PERMIT, 'UNKNOWN_PLATE', None, False)

        # ========== CONDITION 4: Lane Cooldown ==========
        if lane.last_gate_open:
            time_since_open = (now - lane.last_gate_open).total_seconds()
            if time_since_open < cooldown_seconds:
                remaining = cooldown_seconds - time_since_open
                logger.info(
                    f"TAILGATE_BLOCKED - Lane in cooldown: {remaining:.1f}s remaining [{plate}]"
                )
                return (TAILGATE_BLOCKED, 'LANE_COOLDOWN', matched_entity_id, False)

        # ========== ALL CONDITIONS PASSED - OPEN GATE ==========
        logger.info(f"ACCESS_GRANTED - {reason_code} [{plate}]")
        return (ACCESS_GRANTED, reason_code, matched_entity_id, True)

    def evaluate_exit_lane(
        self,
        plate: str,
        confidence: float,
        lane_id: str
    ) -> Tuple[str, str, Optional[str], bool]:
        """
        EXIT LANE: Just log, no access control, no gate

        Args:
            plate: License plate number
            confidence: OCR confidence score
            lane_id: Lane identifier

        Returns:
            Tuple of (decision, reason_code, matched_entity_id, should_open_gate)
            Always returns (EXIT_LOG, 'EXIT_LANE', None, False)
        """
        logger.info(f"EXIT_LOG - Logging plate: {plate}")
        return (EXIT_LOG, 'EXIT_LANE', None, False)

    def manual_override(
        self,
        lane_id: str,
        operator_id: str = None
    ) -> Tuple[str, str, Optional[str], bool]:
        """
        MANUAL OVERRIDE: Operator manually opens gate

        Args:
            lane_id: Lane identifier
            operator_id: ID of operator triggering override

        Returns:
            Tuple of (decision, reason_code, matched_entity_id, should_open_gate)
        """
        logger.info(f"MANUAL_OVERRIDE - Operator: {operator_id} Lane: {lane_id}")
        return (MANUAL_OVERRIDE, 'OPERATOR_OVERRIDE', operator_id, True)
