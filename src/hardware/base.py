"""
Hardware abstraction layer for GPIO control across different platforms
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class RelayConfig:
    """Configuration for relay control"""
    pin: int
    pulse_duration: float = 5.0
    active_high: bool = True


class HardwareInterface(ABC):
    """Abstract base class for hardware control"""

    def __init__(self):
        self.relay_config: Optional[RelayConfig] = None
        self._initialized = False

    @abstractmethod
    def setup_gpio(self, relay_config: RelayConfig) -> None:
        """Initialize GPIO pins"""
        pass

    @abstractmethod
    def trigger_relay(self, duration: Optional[float] = None) -> bool:
        """
        Trigger relay for gate control

        Args:
            duration: Pulse duration in seconds (uses config default if None)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up GPIO resources"""
        pass

    @abstractmethod
    def get_system_info(self) -> dict:
        """Get hardware system information"""
        pass

    @abstractmethod
    def get_cpu_temp(self) -> Optional[float]:
        """Get CPU temperature in Celsius"""
        pass

    @abstractmethod
    def get_gpu_temp(self) -> Optional[float]:
        """Get GPU temperature in Celsius"""
        pass

    @abstractmethod
    def get_cpu_usage(self) -> Optional[float]:
        """Get CPU usage percentage (0-100)"""
        pass

    @abstractmethod
    def get_platform_version(self) -> Optional[str]:
        """Get platform/OS version string (e.g. JetPack version)"""
        pass

    def is_initialized(self) -> bool:
        """Check if hardware is initialized"""
        return self._initialized
