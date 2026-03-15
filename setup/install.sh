#!/usr/bin/env bash
# =============================================================================
# Coin Reader — Raspberry Pi Setup Script
# Run as root: sudo bash setup/install.sh
# =============================================================================
set -euo pipefail

INSTALL_DIR="/home/pi/coin-reader"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================="
echo " Coin Reader — Raspberry Pi Setup"
echo "============================================="

# ── System packages ──────────────────────────────────────────────────────
echo ""
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    python3-evdev \
    pulseaudio-utils \
    mpg321 \
    mpg123 \
    git \
    uhubctl \
    alsa-utils

# ── USB input permissions ────────────────────────────────────────────────
echo ""
echo "[2/6] Setting up USB input device permissions..."
# Allow the service (running as root) to grab input devices.
# For non-root usage, add user to the 'input' group:
if ! groups pi 2>/dev/null | grep -q input; then
    usermod -aG input pi 2>/dev/null || true
    echo "  Added 'pi' user to 'input' group"
fi

# Create udev rule so the RFID reader is accessible
cat > /etc/udev/rules.d/99-rfid-reader.rules << 'UDEV'
# Allow access to USB RFID readers for the 'input' group
SUBSYSTEM=="input", ATTRS{idProduct}=="*", ATTRS{idVendor}=="*", MODE="0660", GROUP="input"
UDEV
udevadm control --reload-rules
udevadm trigger
echo "  USB input permissions configured"

# ── Audio setup ──────────────────────────────────────────────────────────
echo ""
echo "[3/6] Configuring audio..."
amixer set PCM unmute 2>/dev/null || true
amixer set PCM 100% 2>/dev/null || true
echo "  Audio configured"

# ── Install project ──────────────────────────────────────────────────────
echo ""
echo "[4/6] Installing project to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r "$PROJECT_DIR"/* "$INSTALL_DIR/"

cd "$INSTALL_DIR"
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ── Environment file ─────────────────────────────────────────────────────
echo ""
echo "[5/6] Setting up environment file..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cat > "$INSTALL_DIR/.env" << 'ENVEOF'
# Twilio credentials (for WhatsApp/SMS)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# WhatsApp Cloud API (alternative to Twilio)
WHATSAPP_PHONE_ID=
WHATSAPP_TOKEN=
ENVEOF
    chmod 600 "$INSTALL_DIR/.env"
    echo "  Created .env file — edit it with your credentials"
else
    echo "  .env already exists, skipping"
fi

# ── Systemd service ──────────────────────────────────────────────────────
echo ""
echo "[6/6] Installing systemd service..."
sed "s|/usr/bin/python3|$INSTALL_DIR/venv/bin/python3|" \
    "$INSTALL_DIR/setup/systemd/coin-reader.service" > /etc/systemd/system/coin-reader.service

systemctl daemon-reload
systemctl enable coin-reader.service
echo "  Service installed and enabled"

# ── Done ─────────────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo " Setup complete!"
echo "============================================="
echo ""
echo " Next steps:"
echo "   1. Plug in your USB RFID reader"
echo "   2. Edit $INSTALL_DIR/.env with your API credentials"
echo "   3. Edit $INSTALL_DIR/config.yaml to match your hardware"
echo "   4. Edit $INSTALL_DIR/coins.yaml to define your NFC tags"
echo "   5. Add sound files to $INSTALL_DIR/sounds/"
echo "   6. Start the service: sudo systemctl start coin-reader"
echo "   7. View logs: journalctl -u coin-reader -f"
echo ""
echo " Identify your reader:"
echo "   python3 -c \"import evdev; [print(d.path, d.name) for d in [evdev.InputDevice(p) for p in evdev.list_devices()]]\""
echo ""
echo " Test without NFC hardware:"
echo "   cd $INSTALL_DIR && venv/bin/python3 -m src.main --simulate AA:BB:CC:DD"
echo ""
