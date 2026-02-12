"""
Hardware factory for Jetson Nano platform detection and instantiation
"""
import platform
import logging
from typing import Optional

from .base import HardwareInterface
from .jetson_nano import JetsonNanoHardware

logger = logging.getLogger(__name__)


def detect_hardware() -> str:
    """
    Detect hardware platform.

    Returns:
        "JETSON_NANO" or "GENERIC"
    """
    try:
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read()
            if 'tegra' in cpuinfo.lower():
                return 'JETSON_NANO'
    except FileNotFoundError:
        logger.warning("Could not read /proc/cpuinfo")

    # Check device-tree model
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip().rstrip('\x00')
            if 'jetson' in model.lower():
                return 'JETSON_NANO'
    except FileNotFoundError:
        pass

    logger.warning(f"Unknown hardware platform: {platform.machine()}")
    return 'GENERIC'


def get_hardware_interface(hardware_type: Optional[str] = None) -> HardwareInterface:
    """
    Get hardware interface for the platform.

    Args:
        hardware_type: Override auto-detection with "JETSON_NANO" or "GENERIC"

    Returns:
        HardwareInterface implementation

    Raises:
        ValueError: If hardware type is not supported
    """
    if hardware_type is None:
        hardware_type = detect_hardware()

    logger.info(f"Initializing hardware interface for: {hardware_type}")

    if hardware_type == 'JETSON_NANO':
        return JetsonNanoHardware()
    else:
        raise ValueError(f"Unsupported hardware type: {hardware_type}. This device requires NVIDIA Jetson Nano.")
