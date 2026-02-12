# ANPR Edge Device

Edge-based ANPR access controller for NVIDIA Jetson Nano.

## Features

- **100% Offline Capable**: All access decisions made locally
- **GPU-Accelerated ANPR**: Plate Recognizer Stream on Jetson (CUDA)
- **GPIO Relay Control**: Direct gate motor control via Jetson.GPIO
- **Multi-Read Confirmation**: Anti-tailgating protection
- **Async Cloud Sync**: Configuration downloads and event uploads
- **Persistent Storage**: Events stored until cloud acknowledgment

## Hardware

- NVIDIA Jetson Nano (JetPack 4.6.x / 5.x / 6.x)
- Jetson.GPIO for relay control

## Installation

```bash
sudo ./scripts/install.sh
```

## Configuration

Edit `config/config.yaml`:
- Device ID and API key
- Cloud API URL
- GPIO pin configuration
- Access control thresholds

Set JetPack version in `.env`:
```bash
# JetPack 4.6.x (R32): latest
# JetPack 5.x   (R35): r35
# JetPack 6.x   (R36): r36
JETPACK_TAG=latest
```

## Usage

```bash
# Start service
sudo systemctl start edge-device

# View logs
sudo journalctl -u edge-device -f

# Check status
sudo systemctl status edge-device
```

## Architecture

Entry Lane (IN):
- Confidence >= threshold
- Multi-read confirmation (anti-tailgating)
- Check permit or guest pass
- Lane cooldown check
- Fire relay if all conditions pass

Exit Lane (OUT):
- Log only, no gate control

## License

Proprietary
