# Coin Reader

NFC-powered action system for Raspberry Pi. Scan an NFC tag ("coin") and the Pi executes configurable actions: light patterns, sounds, Sonos music, and WhatsApp/SMS messages.

## How It Works

1. The USB NFC reader waits for a tag to be scanned
2. When a tag is scanned, its UID is looked up in `coins.yaml`
3. Three outcomes:
   - **Unknown tag** → flash red + error sound
   - **Expired coin** → flash red + error sound
   - **Valid coin** → execute the configured action chain

## Hardware

### Required Components

| Component | Model | Notes |
|-----------|-------|-------|
| Raspberry Pi | Pi 4 or Pi 5 | Any model with USB ports |
| NFC Reader | 13.56 MHz USB RFID module | Plugs into USB, no wiring needed |
| LED Strip | WS2812B / NeoPixel (5V) | For programmable patterns. See LED section below |
| Speaker | Any USB or 3.5mm speaker | For sound effects |
| NFC Tags | MIFARE Classic 1K | 13.56 MHz, any form factor |

### NFC Reader (USB)

The 13.56 MHz USB RFID reader simply plugs into any USB port on the Pi. No wiring, no soldering, no SPI configuration. It works in one of two modes:

- **HID keyboard mode** (most common): The reader appears as a keyboard and "types" the tag UID when scanned. The software uses `evdev` to capture these keystrokes exclusively.
- **Serial mode**: Some readers appear as a virtual serial port (`/dev/ttyUSB0`). Set `mode: serial` in config if yours works this way.

**First-time setup — identify your reader:**

```bash
# List all input devices to find your reader
python3 -c "import evdev; [print(d.path, d.name, d.phys) for d in [evdev.InputDevice(p) for p in evdev.list_devices()]]"

# If it shows up, you're in HID mode (default config works)
# If not, check for serial: ls /dev/ttyUSB* /dev/ttyACM*
```

**Discovering tag UIDs:**

Just run the system and scan a tag. Unknown UIDs are logged:
```
2026-02-23 12:00:00 [WARNING] coin_handler: Unknown tag: 0A:1B:2C:3D
```
Copy that UID into `coins.yaml` to register it.

### Wiring: WS2812B LED Strip → Raspberry Pi

```
Strip Wire   Pi Pin (BCM)    Notes
──────────   ────────────    ─────
DIN (Data)   GPIO 18         Must be PWM-capable (GPIO 18 or 12)
+5V          5V Pin 2        Or external 5V supply for long strips
GND          GND Pin 6       Common ground with Pi
```

> **Important:** For strips with more than ~30 LEDs, use an external 5V power supply (not the Pi's 5V pin) to avoid brownouts. Always connect grounds together.

### About the IKEA LED Strip

IKEA USB LED strips (KABBLEKA, VATTENSTEN) are **not individually addressable** — they can't do per-LED patterns. Your options:

1. **WS2812B strip** (recommended): Full pattern support — rainbow, chase, sparkle, etc. These are cheap (~€5-10 for 1-5m) and connect to GPIO for data + USB/external for power.
2. **IKEA USB strip as-is**: The system can do basic on/off via USB power control (`uhubctl`). Set `led.type: usb` in config. No patterns, just power toggling.
3. **Hack an IKEA VATTENSTEN**: Cut the proprietary connector, wire RGB lines through transistors to GPIO pins. Gives whole-strip color control but not per-LED patterns.

### Speaker

Connect any speaker via:
- **3.5mm audio jack** (default audio output)
- **USB audio adapter** (set as default in ALSA config)
- **Bluetooth** (requires additional pairing setup)

## Software Setup

### Quick Install (on the Pi)

```bash
git clone <your-repo-url> /home/pi/coin-reader
cd /home/pi/coin-reader
sudo bash setup/install.sh
```

This installs all dependencies, configures USB input permissions, sets up a Python virtualenv, and installs a systemd service.

### Manual Install

```bash
sudo apt install python3-pip python3-venv python3-evdev pulseaudio-utils mpg321 mpg123 uhubctl

python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

**`config.yaml`** — Hardware and service settings:
- NFC reader mode (HID or serial), device path, UID format
- LED strip type, pin, number of LEDs, brightness
- Audio device, volume
- Sonos speaker name/IP
- Messaging provider credentials

**`coins.yaml`** — The coin database:
- Map NFC UIDs to names, expiry dates, and action chains
- See the file for examples of all action types

**`.env`** — API secrets (Twilio, WhatsApp):
```bash
cp .env.example .env
nano .env  # fill in your credentials
```

## Usage

### Run Directly

```bash
source venv/bin/activate
sudo python3 -m src.main        # sudo needed for evdev device access
```

### Run as Service

```bash
sudo systemctl start coin-reader    # start
sudo systemctl stop coin-reader     # stop
sudo systemctl status coin-reader   # check status
journalctl -u coin-reader -f        # live logs
```

### Test Without Hardware

Simulate a tag scan:

```bash
python3 -m src.main --simulate AA:BB:CC:DD
```

## Defining Coins

Edit `coins.yaml` to add your NFC tags. First, scan a tag to find its UID (it will show in the logs as "Unknown tag: XX:XX:XX:XX"), then add it:

```yaml
coins:
  "XX:XX:XX:XX":
    name: "My Coin"
    expires: "2026-12-31"    # null = never expires
    actions:
      - type: led_pattern
        pattern: rainbow      # rainbow, pulse, breathe, chase, sparkle, wave, flash, solid
        duration: 30
        speed: 1.5

      - type: play_sound
        file: my_sound.wav    # place in sounds/ directory

      - type: sonos_play
        favorite: "My Playlist"  # or use uri: "x-rincon-..."
        volume: 30

      - type: send_message
        to: "+31612345678"
        message: "The coin was used!"
        method: whatsapp      # whatsapp or sms

      - type: wait
        seconds: 2

      - type: sonos_control
        command: volume       # pause, stop, next, previous, volume, group_all
        level: 50
```

## UID Format

USB RFID readers may output UIDs in different formats depending on the reader. The system handles all common formats:

| Reader output | `uid_format` setting | Stored in coins.yaml as |
|--------------|---------------------|------------------------|
| `0012345678` (decimal) | `hex` (default) | `00:BC:61:4E` |
| `AABBCCDD` (hex) | `hex` (default) | `AA:BB:CC:DD` |
| `0012345678` (decimal) | `decimal` | `0012345678` |
| anything | `raw` | exactly as received |

If your reader outputs 10-digit decimal numbers (most common for USB readers), the default `hex` format converts them to colon-separated hex. If you prefer to keep decimal UIDs, set `uid_format: decimal` in config and use decimal UIDs in `coins.yaml`.

## Finding Sonos URIs

To find URIs for your Sonos content:

```python
import soco
speaker = soco.discovery.any_soco()

# List favorites
for fav in speaker.music_library.get_sonos_favorites():
    print(f"{fav.title}: {fav.get_uri()}")

# Use favorite by name (easier)
# In coins.yaml, use: favorite: "My Playlist Name"
```

## Project Structure

```
├── config.yaml              # Hardware & service configuration
├── coins.yaml               # NFC tag → action mapping database
├── requirements.txt         # Python dependencies
├── .env.example             # Template for API secrets
├── sounds/                  # Sound files (.wav, .mp3)
├── src/
│   ├── main.py              # Entry point, signal handling
│   ├── nfc_reader.py        # USB NFC reader (evdev HID or serial)
│   ├── led_controller.py    # WS2812B patterns + USB fallback
│   ├── audio_player.py      # Local speaker via paplay/mpg321/mpg123
│   ├── sonos_controller.py  # Sonos via SoCo library
│   ├── messenger.py         # WhatsApp/SMS via Twilio or Cloud API
│   └── coin_handler.py      # Action orchestrator
└── setup/
    ├── install.sh           # Automated Pi setup script
    └── systemd/
        └── coin-reader.service
```
