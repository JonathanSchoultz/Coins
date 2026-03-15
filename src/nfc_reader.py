"""
NFC/RFID reader module for USB readers.

Supports two common USB reader modes:
  - HID keyboard emulation (reader "types" the UID, read via evdev)
  - Virtual serial port (reader sends UID over /dev/ttyUSB0 or similar)

Most cheap 13.56MHz USB RFID readers use keyboard emulation mode.
"""

import logging
import os
import time
import threading
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    import evdev
    from evdev import InputDevice, categorize, ecodes
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# evdev key codes → characters for decoding HID keyboard output
_KEY_MAP = {
    ecodes.KEY_0: "0", ecodes.KEY_1: "1", ecodes.KEY_2: "2",
    ecodes.KEY_3: "3", ecodes.KEY_4: "4", ecodes.KEY_5: "5",
    ecodes.KEY_6: "6", ecodes.KEY_7: "7", ecodes.KEY_8: "8",
    ecodes.KEY_9: "9",
    # Some RFID readers emit keypad scancodes instead of top-row digits.
    ecodes.KEY_KP0: "0", ecodes.KEY_KP1: "1", ecodes.KEY_KP2: "2",
    ecodes.KEY_KP3: "3", ecodes.KEY_KP4: "4", ecodes.KEY_KP5: "5",
    ecodes.KEY_KP6: "6", ecodes.KEY_KP7: "7", ecodes.KEY_KP8: "8",
    ecodes.KEY_KP9: "9",
    ecodes.KEY_A: "A", ecodes.KEY_B: "B", ecodes.KEY_C: "C",
    ecodes.KEY_D: "D", ecodes.KEY_E: "E", ecodes.KEY_F: "F",
    ecodes.KEY_G: "G", ecodes.KEY_H: "H", ecodes.KEY_I: "I",
    ecodes.KEY_J: "J", ecodes.KEY_K: "K", ecodes.KEY_L: "L",
    ecodes.KEY_M: "M", ecodes.KEY_N: "N", ecodes.KEY_O: "O",
    ecodes.KEY_P: "P", ecodes.KEY_Q: "Q", ecodes.KEY_R: "R",
    ecodes.KEY_S: "S", ecodes.KEY_T: "T", ecodes.KEY_U: "U",
    ecodes.KEY_V: "V", ecodes.KEY_W: "W", ecodes.KEY_X: "X",
    ecodes.KEY_Y: "Y", ecodes.KEY_Z: "Z",
    ecodes.KEY_SEMICOLON: ";", ecodes.KEY_SLASH: "/",
    ecodes.KEY_DOT: ".", ecodes.KEY_COMMA: ",",
    ecodes.KEY_MINUS: "-", ecodes.KEY_EQUAL: "=",
    ecodes.KEY_SPACE: " ",
} if EVDEV_AVAILABLE else {}


def find_usb_rfid_device(device_name: Optional[str] = None,
                         device_path: Optional[str] = None) -> Optional[str]:
    """
    Auto-detect the USB RFID reader from /dev/input/event* devices.
    Returns the device path or None.
    """
    if not EVDEV_AVAILABLE:
        return None

    if device_path and os.path.exists(device_path):
        return device_path

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for dev in devices:
        name_lower = dev.name.lower()
        if device_name and device_name.lower() in name_lower:
            logger.info("Found RFID reader by name: %s at %s", dev.name, dev.path)
            return dev.path
        # Common USB RFID reader identifiers
        if any(keyword in name_lower for keyword in ["rfid", "hid", "card reader", "rf reader"]):
            logger.info("Auto-detected RFID reader: %s at %s", dev.name, dev.path)
            return dev.path

    # If no obvious RFID reader found, list all devices for debugging
    if devices:
        logger.warning("Could not auto-detect RFID reader. Available input devices:")
        for dev in devices:
            logger.warning("  %s: %s (phys: %s)", dev.path, dev.name, dev.phys)
    else:
        logger.error("No input devices found at all")

    return None


class NFCReader:
    """Reads NFC tag UIDs from a USB RFID reader."""

    def __init__(self, config: dict):
        self.mode = config.get("mode", "hid")  # "hid" or "serial"
        self.debounce_time = config.get("debounce_time", 2.0)
        self.uid_format = config.get("uid_format", "hex")  # "hex", "decimal", "raw"
        # Some HID readers occasionally emit truncated trailing fragments.
        self.min_uid_bytes = int(config.get("min_uid_bytes", 4))
        self.min_decimal_digits = int(config.get("min_decimal_digits", 8))

        # HID mode settings
        self._device_name = config.get("device_name")
        self._device_path = config.get("device_path")
        self._grab_device = config.get("grab_device", True)

        # Serial mode settings
        self._serial_port = config.get("serial_port", "/dev/ttyUSB0")
        self._serial_baud = config.get("serial_baud", 9600)

        self._last_uid: Optional[str] = None
        self._last_read_time: float = 0
        self._running = False
        self._device = None
        self._serial = None

    def poll_loop(self, on_tag: Callable[[str], None]):
        """Continuously read tags. Calls on_tag(uid_str) on each valid read."""
        self._running = True

        if self.mode == "serial":
            self._poll_serial(on_tag)
        else:
            self._poll_hid(on_tag)

    def _poll_hid(self, on_tag: Callable[[str], None]):
        """Read UIDs from a USB HID keyboard-emulating reader via evdev."""
        if not EVDEV_AVAILABLE:
            logger.error("evdev not installed — run: pip install evdev")
            return

        device_path = find_usb_rfid_device(self._device_name, self._device_path)
        if not device_path:
            logger.error(
                "No RFID reader found. Plug in the reader and check with: "
                "python3 -c \"import evdev; [print(d.path, d.name) "
                "for d in [evdev.InputDevice(p) for p in evdev.list_devices()]]\""
            )
            return

        self._device = InputDevice(device_path)
        logger.info("Opened RFID reader: %s (%s)", self._device.name, device_path)

        if self._grab_device:
            try:
                self._device.grab()
                logger.info("Grabbed exclusive access to reader")
            except OSError:
                logger.warning("Could not grab device — run as root or adjust permissions")

        logger.info("Waiting for NFC tags (HID mode)...")
        buffer = []

        try:
            for event in self._device.read_loop():
                if not self._running:
                    break

                if event.type != ecodes.EV_KEY:
                    continue

                key_event = categorize(event)
                if key_event.keystate != key_event.key_down:
                    continue

                if key_event.scancode in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                    if buffer:
                        raw_uid = "".join(buffer)
                        uid = self._format_uid(raw_uid)
                        buffer.clear()
                        self._handle_uid(uid, on_tag)
                else:
                    char = _KEY_MAP.get(key_event.scancode, "")
                    if char:
                        buffer.append(char)

        except OSError as e:
            if self._running:
                logger.error("Reader disconnected: %s", e)
        finally:
            self._release_device()

    def _poll_serial(self, on_tag: Callable[[str], None]):
        """Read UIDs from a USB serial RFID reader."""
        if not SERIAL_AVAILABLE:
            logger.error("pyserial not installed — run: pip install pyserial")
            return

        try:
            self._serial = serial.Serial(
                port=self._serial_port,
                baudrate=self._serial_baud,
                timeout=1,
            )
            logger.info("Opened serial RFID reader: %s @ %d baud",
                        self._serial_port, self._serial_baud)
        except serial.SerialException:
            logger.exception("Could not open serial port %s", self._serial_port)
            return

        logger.info("Waiting for NFC tags (serial mode)...")

        while self._running:
            try:
                line = self._serial.readline().decode("ascii", errors="ignore").strip()
                if line:
                    uid = self._format_uid(line)
                    self._handle_uid(uid, on_tag)
            except Exception:
                if self._running:
                    logger.exception("Serial read error")
                    time.sleep(1)

        if self._serial and self._serial.is_open:
            self._serial.close()

    def _handle_uid(self, uid: str, on_tag: Callable[[str], None]):
        """Process a UID with debouncing."""
        if not self._is_plausible_uid(uid):
            logger.debug("Ignoring short/invalid UID fragment: %s", uid)
            return

        now = time.time()
        if uid == self._last_uid and (now - self._last_read_time) < self.debounce_time:
            logger.debug("Debounced duplicate: %s", uid)
            return

        self._last_uid = uid
        self._last_read_time = now
        logger.info("Tag detected: %s", uid)
        on_tag(uid)

    def _is_plausible_uid(self, uid: str) -> bool:
        """Filter obvious partial UID fragments produced by noisy HID readers."""
        text = str(uid).strip().upper()
        if not text:
            return False

        if self.uid_format == "decimal":
            digits = "".join(ch for ch in text if ch.isdigit())
            return len(digits) >= self.min_decimal_digits

        # hex/raw readers commonly produce hexadecimal IDs.
        hex_compact = "".join(ch for ch in text if ch in "0123456789ABCDEF")
        if len(hex_compact) < self.min_uid_bytes * 2:
            return False
        # Require full bytes.
        return len(hex_compact) % 2 == 0

    def _format_uid(self, raw: str) -> str:
        """
        Normalize the UID into the format used in coins.yaml.

        USB readers may output:
          - Decimal: "0012345678" (10 digits)
          - Hex:     "AABBCCDD"
          - Colon-separated: "AA:BB:CC:DD"

        The uid_format config determines how to store/match UIDs.
        """
        raw = raw.strip().upper()

        if self.uid_format == "raw":
            return raw

        if self.uid_format == "decimal":
            # Keep as-is if already decimal, or convert hex→decimal
            try:
                if all(c in "0123456789" for c in raw):
                    return raw
                return str(int(raw.replace(":", ""), 16))
            except ValueError:
                return raw

        # Default: hex with colons
        hex_str = raw.replace(":", "").replace(" ", "")

        if all(c in "0123456789" for c in hex_str):
            # Input is decimal — convert to hex
            try:
                num = int(hex_str)
                hex_str = f"{num:08X}"
            except ValueError:
                return raw

        if all(c in "0123456789ABCDEF" for c in hex_str):
            # Insert colons every 2 characters
            return ":".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))

        return raw

    def stop(self):
        self._running = False
        self._release_device()
        logger.info("NFC reader stopped")

    def _release_device(self):
        if self._device:
            try:
                self._device.ungrab()
            except Exception:
                pass
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    def cleanup(self):
        self.stop()
        if self._serial and self._serial.is_open:
            self._serial.close()
        logger.info("NFC reader cleaned up")
