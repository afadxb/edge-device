#!/usr/bin/env bash
set -euo pipefail

# ANPR Edge Device - Installation Script
# Supports: NVIDIA Jetson Nano
# Run as root: sudo ./scripts/install.sh

INSTALL_DIR="/opt/edge-device"
SERVICE_USER="anpr"
PYTHON_MIN="3.10"
VENV_DIR="${INSTALL_DIR}/venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()   { echo -e "${GREEN}[INSTALL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Pre-flight checks ──────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo)"
    exit 1
fi

log "ANPR Edge Device Installer"
log "=============================="

# Detect platform - must be Jetson
PLATFORM="unknown"
if [[ -f /proc/device-tree/model ]]; then
    MODEL=$(tr -d '\0' < /proc/device-tree/model)
    if echo "$MODEL" | grep -qi "jetson"; then
        PLATFORM="jetson"
    fi
fi

if [[ "${PLATFORM}" != "jetson" ]]; then
    # Fallback: check cpuinfo for tegra
    if grep -qi "tegra" /proc/cpuinfo 2>/dev/null; then
        PLATFORM="jetson"
    fi
fi

if [[ "${PLATFORM}" != "jetson" ]]; then
    error "This installer requires an NVIDIA Jetson Nano device."
    error "Detected: ${MODEL:-unknown} ($(uname -m))"
    exit 1
fi

log "Detected platform: NVIDIA Jetson Nano ($(uname -m))"

# Detect JetPack version
JETPACK="unknown"
if [[ -f /etc/nv_tegra_release ]]; then
    JETPACK_LINE=$(head -1 /etc/nv_tegra_release)
    log "Tegra release: ${JETPACK_LINE}"
    if echo "$JETPACK_LINE" | grep -q "R32"; then
        JETPACK="4.x"
    elif echo "$JETPACK_LINE" | grep -q "R35"; then
        JETPACK="5.x"
    elif echo "$JETPACK_LINE" | grep -q "R36"; then
        JETPACK="6.x"
    fi
fi
log "JetPack version: ${JETPACK}"

# ── System dependencies ────────────────────────────────────────
log "Installing system dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    gcc \
    docker.io \
    docker-compose \
    nvidia-container-toolkit \
    curl \
    sqlite3

# Check Python version
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python version: ${PYTHON_VER}"

# ── Create service user ────────────────────────────────────────
if ! id "${SERVICE_USER}" &>/dev/null; then
    log "Creating service user: ${SERVICE_USER}"
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# Add user to gpio and docker groups
usermod -aG gpio "${SERVICE_USER}" 2>/dev/null || true
usermod -aG docker "${SERVICE_USER}" 2>/dev/null || true

# ── Install application ────────────────────────────────────────
log "Setting up application in ${INSTALL_DIR}..."

# Create directories
mkdir -p "${INSTALL_DIR}"/{data,logs,config}

# Copy application files (if running from repo)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
    log "Copying application files from ${SCRIPT_DIR}..."
    cp -r "${SCRIPT_DIR}/src" "${INSTALL_DIR}/"
    cp -r "${SCRIPT_DIR}/config"/*.template "${INSTALL_DIR}/config/" 2>/dev/null || true
    cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/docker-compose.yml" "${INSTALL_DIR}/"
    cp "${SCRIPT_DIR}/Dockerfile" "${INSTALL_DIR}/"
fi

# ── Python virtual environment ─────────────────────────────────
log "Creating Python virtual environment..."
python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

log "Installing Python dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r "${INSTALL_DIR}/requirements.txt"

# Install Jetson GPIO library
log "Installing Jetson GPIO library..."
pip install Jetson.GPIO

deactivate

# ── Configuration ──────────────────────────────────────────────
if [[ ! -f "${INSTALL_DIR}/config/config.yaml" ]]; then
    if [[ -f "${INSTALL_DIR}/config/config.yaml.template" ]]; then
        cp "${INSTALL_DIR}/config/config.yaml.template" "${INSTALL_DIR}/config/config.yaml"
        warn "Created config.yaml from template - edit before starting!"
    fi
fi

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    if [[ -f "${SCRIPT_DIR}/.env.example" ]]; then
        cp "${SCRIPT_DIR}/.env.example" "${INSTALL_DIR}/.env"
        warn "Created .env from example - edit before starting!"
    fi
fi

# ── Systemd service ────────────────────────────────────────────
log "Installing systemd service..."
cat > /etc/systemd/system/edge-device.service << EOF
[Unit]
Description=ANPR Edge Device
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}/src
Environment=PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin
Environment=EDGE_CONFIG_PATH=${INSTALL_DIR}/config/config.yaml
EnvironmentFile=-${INSTALL_DIR}/.env
ExecStart=${VENV_DIR}/bin/python -m main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=edge-device

# Security hardening
NoNewPrivileges=false
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR}/data ${INSTALL_DIR}/logs
SupplementaryGroups=gpio docker

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ── Set permissions ────────────────────────────────────────────
log "Setting file permissions..."
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 600 "${INSTALL_DIR}/.env" 2>/dev/null || true
chmod 600 "${INSTALL_DIR}/config/config.yaml" 2>/dev/null || true

# ── Done ───────────────────────────────────────────────────────
log ""
log "Installation complete!"
log "=============================="
log ""
log "Next steps:"
log "  1. Edit configuration:"
log "     sudo nano ${INSTALL_DIR}/config/config.yaml"
log "     sudo nano ${INSTALL_DIR}/.env"
log ""
log "  2. Register this device with the cloud API:"
log "     sudo ${VENV_DIR}/bin/python ${INSTALL_DIR}/scripts/register_device.py \\"
log "       --api-url https://api.anpr.cloud"
log ""
log "  3. Start Plate Recognizer Stream:"
log "     cd ${INSTALL_DIR} && docker-compose up -d stream"
log ""
log "  4. Start the edge device:"
log "     sudo systemctl start edge-device"
log "     sudo systemctl enable edge-device"
log ""
log "  5. Check status:"
log "     sudo systemctl status edge-device"
log "     sudo journalctl -u edge-device -f"
