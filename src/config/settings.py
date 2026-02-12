"""
Configuration loader for edge device settings

Loads from config/config.yaml with environment variable overrides.
"""
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get('EDGE_CONFIG_PATH', 'config/config.yaml')


@dataclass
class DeviceConfig:
    controller_id: str = ''
    api_key: str = ''


@dataclass
class ApiConfig:
    base_url: str = 'https://api.example.com'
    timeout: int = 10


@dataclass
class GpioConfig:
    relay_pin: int = 18
    pulse_duration: float = 5.0
    active_high: bool = True


@dataclass
class StreamConfig:
    webhook_port: int = 8001
    webhook_path: str = '/v1/webhook/stream'
    health_timeout: int = 120


@dataclass
class SyncConfig:
    heartbeat_interval: int = 60
    config_sync_interval: int = 300
    event_batch_size: int = 50
    event_upload_interval: int = 10


@dataclass
class DatabaseConfig:
    path: str = 'data/edge.db'

    @property
    def url(self) -> str:
        return f'sqlite:///{self.path}'


@dataclass
class LoggingConfig:
    level: str = 'INFO'
    file: str = 'logs/edge-controller.log'


@dataclass
class Settings:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_settings(config_path: Optional[str] = None) -> Settings:
    """Load settings from YAML file with environment variable overrides"""
    path = config_path or CONFIG_PATH
    settings = Settings()

    # Load from YAML if exists
    if os.path.exists(path):
        with open(path, 'r') as f:
            raw = yaml.safe_load(f) or {}

        if 'device' in raw:
            settings.device = DeviceConfig(**raw['device'])
        if 'api' in raw:
            settings.api = ApiConfig(**raw['api'])
        if 'gpio' in raw:
            settings.gpio = GpioConfig(**raw['gpio'])
        if 'stream' in raw:
            settings.stream = StreamConfig(**raw['stream'])
        if 'sync' in raw:
            settings.sync = SyncConfig(**raw['sync'])
        if 'database' in raw:
            settings.database = DatabaseConfig(**raw['database'])
        if 'logging' in raw:
            settings.logging = LoggingConfig(**raw['logging'])

        logger.info(f"Configuration loaded from {path}")
    else:
        logger.warning(f"Config file not found at {path}, using defaults")

    # Environment variable overrides (highest priority)
    if os.environ.get('EDGE_CONTROLLER_ID'):
        settings.device.controller_id = os.environ['EDGE_CONTROLLER_ID']
    if os.environ.get('EDGE_API_KEY'):
        settings.device.api_key = os.environ['EDGE_API_KEY']
    if os.environ.get('EDGE_API_URL'):
        settings.api.base_url = os.environ['EDGE_API_URL']
    if os.environ.get('EDGE_RELAY_PIN'):
        settings.gpio.relay_pin = int(os.environ['EDGE_RELAY_PIN'])
    if os.environ.get('EDGE_WEBHOOK_PORT'):
        settings.stream.webhook_port = int(os.environ['EDGE_WEBHOOK_PORT'])
    if os.environ.get('EDGE_DB_PATH'):
        settings.database.path = os.environ['EDGE_DB_PATH']
    if os.environ.get('EDGE_LOG_LEVEL'):
        settings.logging.level = os.environ['EDGE_LOG_LEVEL']

    return settings
