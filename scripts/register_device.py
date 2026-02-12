#!/usr/bin/env python3
"""
Register this edge device with the ANPR cloud backend.

The controller must already exist in the cloud dashboard.
This script links the physical device to that controller record
and writes the returned api_key to config.yaml and .env.

Usage:
    python3 register_device.py --api-url http://api.example.com:3000 --controller-id <id>
    python3 register_device.py --api-url http://api.example.com:3000 --controller-id <id> --token <jwt>
"""
import argparse
import getpass
import json
import os
import platform
import socket
import subprocess
import sys
from typing import Optional

import requests
import yaml


INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/edge-device")
CONFIG_PATH = os.path.join(INSTALL_DIR, "config", "config.yaml")
ENV_PATH = os.path.join(INSTALL_DIR, ".env")


def detect_hardware_type():
    """Detect hardware platform: PI5, JETSON_NANO, or GENERIC."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip().rstrip("\x00").lower()
            if "jetson" in model:
                return "JETSON_NANO"
            if "raspberry" in model:
                return "PI5"
    except FileNotFoundError:
        pass
    return "GENERIC"


def get_device_info():
    """Gather device hardware information for registration."""
    info = {
        "hostname": socket.gethostname(),
        "platform": platform.machine(),
        "osVersion": "{} {}".format(platform.system(), platform.release()),
        "pythonVersion": platform.python_version(),
        "hardwareType": detect_hardware_type(),
    }

    # Detect hardware model (display only)
    try:
        with open("/proc/device-tree/model", "r") as f:
            info["model"] = f.read().strip().rstrip("\x00")
    except FileNotFoundError:
        info["model"] = "Unknown"

    # Get MAC address for device fingerprint
    try:
        result = subprocess.run(
            ["cat", "/sys/class/net/eth0/address"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=5,
        )
        if result.returncode == 0:
            info["macAddress"] = result.stdout.strip()
    except Exception:
        pass

    # Get IP address
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["ipAddress"] = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    return info


def authenticate(api_url: str, token: Optional[str]) -> str:
    """Get an auth token - either use provided one or prompt for login."""
    if token:
        return token

    print("\nCloud API authentication required.")
    email = input("Email: ")
    password = getpass.getpass("Password: ")

    resp = requests.post(
        f"{api_url}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]


def register(api_url, auth_token, device_info, controller_id):
    """Register the device with the cloud backend."""
    payload = {
        "controllerId": controller_id,
        "hardwareType": device_info["hardwareType"],
        "osVersion": device_info.get("osVersion"),
        "pythonVersion": device_info.get("pythonVersion"),
        "ipAddress": device_info.get("ipAddress"),
        "macAddress": device_info.get("macAddress"),
    }
    resp = requests.post(
        "{}/edge-devices/register".format(api_url),
        json=payload,
        headers={"Authorization": "Bearer {}".format(auth_token)},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def update_config(controller_id: str, api_key: str, api_url: str) -> None:
    """Write credentials to config.yaml and .env."""
    # Update config.yaml
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    config.setdefault("device", {})
    config["device"]["controller_id"] = controller_id
    config["device"]["api_key"] = api_key
    config.setdefault("api", {})
    config["api"]["base_url"] = api_url

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    os.chmod(CONFIG_PATH, 0o600)
    print(f"  Updated: {CONFIG_PATH}")

    # Update .env
    env_lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r") as f:
            env_lines = f.readlines()

    env_vars = {
        "EDGE_CONTROLLER_ID": controller_id,
        "EDGE_API_KEY": api_key,
        "EDGE_API_URL": api_url,
    }

    # Replace existing or append
    updated_keys = set()
    new_lines = []
    for line in env_lines:
        key = line.split("=", 1)[0].strip()
        if key in env_vars:
            new_lines.append(f"{key}={env_vars[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, val in env_vars.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)
    os.chmod(ENV_PATH, 0o600)
    print(f"  Updated: {ENV_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Register ANPR edge device with cloud backend")
    parser.add_argument("--api-url", required=True, help="Cloud API base URL")
    parser.add_argument("--controller-id", required=True,
                        help="Controller ID from cloud dashboard (must be created first)")
    parser.add_argument("--token", help="Auth token (skips login prompt)")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    controller_id = args.controller_id

    print("ANPR Edge Device Registration")
    print("=" * 40)

    # Gather device info
    device_info = get_device_info()
    print("\nController: {}".format(controller_id))
    print("Hostname:   {}".format(device_info["hostname"]))
    print("Model:      {}".format(device_info.get("model", "Unknown")))
    print("HW Type:    {}".format(device_info["hardwareType"]))
    print("IP:         {}".format(device_info.get("ipAddress", "unknown")))
    print("API:        {}".format(api_url))

    # Authenticate
    try:
        auth_token = authenticate(api_url, args.token)
    except requests.HTTPError as e:
        print("\nAuthentication failed: {}".format(e))
        sys.exit(1)

    # Register
    print("\nRegistering device...")
    try:
        result = register(api_url, auth_token, device_info, controller_id)
    except requests.HTTPError as e:
        print("\nRegistration failed: {}".format(e))
        if e.response is not None:
            print("Response: {}".format(e.response.text))
        sys.exit(1)

    api_key = result.get("apiKey")
    returned_id = result.get("controllerId", controller_id)

    if not api_key:
        print("\nUnexpected response: {}".format(json.dumps(result, indent=2)))
        sys.exit(1)

    print("\nRegistered successfully!")
    print("  Controller ID: {}".format(returned_id))
    print("  API Key:       {}...".format(api_key[:16]))

    # Save credentials
    print("\nSaving credentials...")
    update_config(returned_id, api_key, api_url)

    print("\nDone! Start the service with:")
    print("  sudo systemctl start edge-device")


if __name__ == "__main__":
    main()
