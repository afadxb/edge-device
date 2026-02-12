"""
NVIDIA Jetson Nano hardware implementation using Jetson.GPIO
"""
import logging
import threading
from typing import Optional

try:
    import Jetson.GPIO as GPIO
    JETSON_GPIO_AVAILABLE = True
except ImportError:
    JETSON_GPIO_AVAILABLE = False

from .base import HardwareInterface, RelayConfig

logger = logging.getLogger(__name__)


class JetsonNanoHardware(HardwareInterface):
    """Hardware interface for NVIDIA Jetson Nano"""

    def __init__(self):
        super().__init__()
        if not JETSON_GPIO_AVAILABLE:
            raise ImportError("Jetson.GPIO not installed. Run: pip install Jetson.GPIO")

    def setup_gpio(self, relay_config: RelayConfig) -> None:
        """Initialize GPIO using Jetson.GPIO"""
        try:
            self.relay_config = relay_config

            # Set GPIO mode to BOARD (physical pin numbering)
            GPIO.setmode(GPIO.BOARD)

            # Setup pin as output with inactive state
            # For active_high relays: initial LOW (off). For active_low: initial HIGH (off).
            initial = GPIO.LOW if relay_config.active_high else GPIO.HIGH
            GPIO.setup(relay_config.pin, GPIO.OUT, initial=initial)

            self._initialized = True
            logger.info(
                f"GPIO initialized: Pin {relay_config.pin}, "
                f"active_high={relay_config.active_high}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize GPIO: {e}")
            raise

    def trigger_relay(self, duration: Optional[float] = None) -> bool:
        """Trigger relay to open gate (non-blocking)"""
        if not self._initialized:
            logger.error("GPIO not initialized")
            return False

        try:
            duration = duration or self.relay_config.pulse_duration
            logger.info(f"Triggering relay for {duration} seconds")

            active_state = GPIO.HIGH if self.relay_config.active_high else GPIO.LOW
            inactive_state = GPIO.LOW if self.relay_config.active_high else GPIO.HIGH

            def _pulse():
                try:
                    GPIO.output(self.relay_config.pin, active_state)
                    threading.Event().wait(duration)
                    GPIO.output(self.relay_config.pin, inactive_state)
                    logger.info("Relay trigger completed")
                except Exception as e:
                    logger.error(f"Relay pulse failed: {e}")

            threading.Thread(target=_pulse, daemon=True).start()
            return True
        except Exception as e:
            logger.error(f"Relay trigger failed: {e}")
            return False

    def cleanup(self) -> None:
        """Clean up GPIO resources"""
        try:
            GPIO.cleanup()
            logger.info("GPIO cleaned up")
        except Exception as e:
            logger.error(f"GPIO cleanup error: {e}")
        self._initialized = False

    def get_system_info(self) -> dict:
        """Get Jetson Nano system information"""
        return {
            "hardware": "NVIDIA Jetson Nano",
            "gpio_library": "Jetson.GPIO",
            "cpu_temp": self.get_cpu_temp(),
        }

    def get_cpu_temp(self) -> Optional[float]:
        """Get CPU temperature from thermal zone"""
        try:
            # Jetson has multiple thermal zones
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
                return round(temp, 1)
        except Exception as e:
            logger.warning(f"Could not read CPU temperature: {e}")
            return None
