#!/usr/bin/env python3
"""
Register this edge device with the ANPR cloud backend.

Contacts the cloud API to register the device and writes
the returned controller_id and api_key to config.yaml and .env.

Usage:
    python scripts/register_device.py --api-url https://api.anpr.cloud
    python scripts/register_device.py --api-url https://api.anpr.cloud --token <admin-token>
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


def get_device_info() -> dict:
    """Gather device hardware information for registration."""
    info = {
        "hostname": socket.gethostname(),
        "platform": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }

    # Detect hardware model
    try:
        with open("/proc/device-tree/model", "r") as f:
            info["model"] = f.read().strip().rstrip("\x00")
    except FileNotFoundError:
        info["model"] = "Unknown"

    # Get MAC address for device fingerprint
    try:
        result = subprocess.run(
            ["cat", "/sys/class/net/eth0/address"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["mac_address"] = result.stdout.strip()
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


def register(api_url: str, auth_token: str, device_info: dict, name: str) -> dict:
    """Register the device with the cloud backend."""
    resp = requests.post(
        f"{api_url}/edge-devices/register",
        json={
            "name": name,
            "hostname": device_info["hostname"],
            "hardwareModel": device_info.get("model", "Unknown"),
            "platform": device_info["platform"],
            "macAddress": device_info.get("mac_address"),
        },
        headers={"Authorization": f"Bearer {auth_token}"},
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
    parser.add_argument("--token", help="Auth token (skips login prompt)")
    parser.add_argument("--name", help="Device name (defaults to hostname)")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")

    print("ANPR Edge Device Registration")
    print("=" * 40)

    # Gather device info
    device_info = get_device_info()
    device_name = args.name or device_info["hostname"]
    print(f"\nDevice:   {device_name}")
    print(f"Model:    {device_info.get('model', 'Unknown')}")
    print(f"Platform: {device_info['platform']}")
    print(f"API:      {api_url}")

    # Authenticate
    try:
        auth_token = authenticate(api_url, args.token)
    except requests.HTTPError as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)

    # Register
    print(f"\nRegistering device '{device_name}'...")
    try:
        result = register(api_url, auth_token, device_info, device_name)
    except requests.HTTPError as e:
        print(f"\nRegistration failed: {e}")
        if e.response is not None:
            print(f"Response: {e.response.text}")
        sys.exit(1)

    controller_id = result.get("controllerId") or result.get("id")
    api_key = result.get("apiKey")

    if not controller_id or not api_key:
        print(f"\nUnexpected response: {json.dumps(result, indent=2)}")
        sys.exit(1)

    print(f"\nRegistered successfully!")
    print(f"  Controller ID: {controller_id}")
    print(f"  API Key:       {api_key[:12]}...")

    # Save credentials
    print(f"\nSaving credentials...")
    update_config(controller_id, api_key, api_url)

    print(f"\nDone! Start the service with:")
    print(f"  sudo systemctl start edge-device")


if __name__ == "__main__":
    main()
