"""LED strip controller supporting WS2812B (NeoPixel) and basic USB strips."""

import logging
import math
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import board
    import neopixel
    NEOPIXEL_AVAILABLE = True
except (ImportError, NotImplementedError):
    NEOPIXEL_AVAILABLE = False
    logger.warning("neopixel not available — LED patterns will be simulated")


class LEDController:
    """Controls an LED strip for light patterns and status feedback."""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", True)
        self.led_type = config.get("type", "ws2812b")
        self.num_leds = config.get("num_leds", 30)
        self.brightness = config.get("brightness", 0.5)

        self._strip = None
        self._animation_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if not self.enabled:
            logger.info("LED controller disabled in config")
            return

        if self.led_type == "ws2812b" and NEOPIXEL_AVAILABLE:
            gpio_pin = getattr(board, f"D{config.get('gpio_pin', 18)}")
            self._strip = neopixel.NeoPixel(
                gpio_pin,
                self.num_leds,
                brightness=self.brightness,
                auto_write=False,
                pixel_order=neopixel.GRB,
            )
            logger.info("WS2812B strip initialized: %d LEDs, brightness=%.1f",
                        self.num_leds, self.brightness)
        elif self.led_type == "usb":
            self._usb_hub = config.get("usb_hub_location", "1-1")
            self._usb_port = config.get("usb_port")
            logger.info("USB LED strip mode (uhubctl hub=%s)", self._usb_hub)
        else:
            logger.info("LED strip running in simulation mode")

    # ── Low-level ────────────────────────────────────────────────────────

    def _set_all(self, r: int, g: int, b: int):
        if self._strip:
            self._strip.fill((r, g, b))
            self._strip.show()
        else:
            logger.debug("LED set_all: (%d, %d, %d)", r, g, b)

    def _set_pixel(self, index: int, r: int, g: int, b: int):
        if self._strip and 0 <= index < self.num_leds:
            self._strip[index] = (r, g, b)

    def _show(self):
        if self._strip:
            self._strip.show()

    def _clear(self):
        self._set_all(0, 0, 0)

    def _usb_power(self, on: bool):
        action = "1" if on else "0"
        cmd = ["uhubctl", "-l", self._usb_hub, "-a", action]
        if self._usb_port is not None:
            cmd.extend(["-p", str(self._usb_port)])
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=5)
            logger.debug("USB power %s", "on" if on else "off")
        except FileNotFoundError:
            logger.error("uhubctl not installed — cannot control USB power")
        except subprocess.CalledProcessError as e:
            logger.error("uhubctl failed: %s", e.stderr.decode())

    # ── Animation engine ─────────────────────────────────────────────────

    def _stop_current_animation(self):
        if self._animation_thread and self._animation_thread.is_alive():
            self._stop_event.set()
            self._animation_thread.join(timeout=3)
        self._stop_event.clear()

    def _run_animation(self, func, duration: float, **kwargs):
        self._stop_current_animation()

        def _wrapper():
            start = time.time()
            try:
                func(duration=duration, **kwargs)
            except Exception:
                logger.exception("LED animation error")
            finally:
                self._clear()

        self._animation_thread = threading.Thread(target=_wrapper, daemon=True)
        self._animation_thread.start()

    # ── Patterns ─────────────────────────────────────────────────────────

    def flash_error(self, duration: float = 3.0):
        """Flash red rapidly — used for expired/unknown tags."""
        if self.led_type == "usb":
            self._flash_usb(duration)
            return
        self._run_animation(self._pattern_flash, duration, color=(255, 0, 0), speed=4.0)

    def play_pattern(self, pattern: str, duration: float = 10.0,
                     color: Optional[list] = None, speed: float = 1.0):
        """Play a named pattern."""
        color_tuple = tuple(color) if color else (255, 255, 255)

        if self.led_type == "usb":
            self._usb_power(True)
            threading.Timer(duration, lambda: self._usb_power(False)).start()
            return

        patterns = {
            "rainbow": self._pattern_rainbow,
            "pulse": self._pattern_pulse,
            "breathe": self._pattern_breathe,
            "chase": self._pattern_chase,
            "flash": self._pattern_flash,
            "sparkle": self._pattern_sparkle,
            "wave": self._pattern_wave,
            "solid": self._pattern_solid,
        }

        func = patterns.get(pattern, self._pattern_solid)
        logger.info("Playing LED pattern '%s' for %.0fs", pattern, duration)
        self._run_animation(func, duration, color=color_tuple, speed=speed)

    def _pattern_flash(self, duration: float, color=(255, 0, 0), speed=4.0, **_):
        end = time.time() + duration
        while time.time() < end and not self._stop_event.is_set():
            self._set_all(*color)
            time.sleep(0.5 / speed)
            if self._stop_event.is_set():
                break
            self._clear()
            time.sleep(0.5 / speed)

    def _pattern_rainbow(self, duration: float, speed=1.0, **_):
        end = time.time() + duration
        offset = 0
        while time.time() < end and not self._stop_event.is_set():
            for i in range(self.num_leds):
                hue = ((i / self.num_leds) + offset) % 1.0
                r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
                self._set_pixel(i, r, g, b)
            self._show()
            offset += 0.01 * speed
            time.sleep(0.02)

    def _pattern_pulse(self, duration: float, color=(255, 255, 255), speed=1.0, **_):
        end = time.time() + duration
        t = 0
        while time.time() < end and not self._stop_event.is_set():
            brightness = (math.sin(t * speed * 2) + 1) / 2
            r = int(color[0] * brightness)
            g = int(color[1] * brightness)
            b = int(color[2] * brightness)
            self._set_all(r, g, b)
            t += 0.05
            time.sleep(0.02)

    def _pattern_breathe(self, duration: float, color=(255, 255, 255), speed=1.0, **_):
        """Slower, smoother breathing effect."""
        end = time.time() + duration
        t = 0
        while time.time() < end and not self._stop_event.is_set():
            brightness = (math.sin(t * speed * 0.8) + 1) / 2
            brightness = brightness ** 2.2  # gamma correction for perceived linearity
            r = int(color[0] * brightness)
            g = int(color[1] * brightness)
            b = int(color[2] * brightness)
            self._set_all(r, g, b)
            t += 0.03
            time.sleep(0.02)

    def _pattern_chase(self, duration: float, color=(255, 255, 255), speed=1.0, **_):
        end = time.time() + duration
        pos = 0
        tail_length = max(3, self.num_leds // 5)
        while time.time() < end and not self._stop_event.is_set():
            for i in range(self.num_leds):
                dist = (i - pos) % self.num_leds
                if dist < tail_length:
                    factor = 1.0 - (dist / tail_length)
                    self._set_pixel(i, int(color[0] * factor),
                                    int(color[1] * factor), int(color[2] * factor))
                else:
                    self._set_pixel(i, 0, 0, 0)
            self._show()
            pos = (pos + 1) % self.num_leds
            time.sleep(0.05 / speed)

    def _pattern_sparkle(self, duration: float, color=(255, 255, 255), speed=1.0, **_):
        import random
        end = time.time() + duration
        while time.time() < end and not self._stop_event.is_set():
            self._clear()
            count = max(1, self.num_leds // 6)
            for _ in range(count):
                idx = random.randint(0, self.num_leds - 1)
                self._set_pixel(idx, *color)
            self._show()
            time.sleep(0.1 / speed)

    def _pattern_wave(self, duration: float, color=(255, 255, 255), speed=1.0, **_):
        end = time.time() + duration
        t = 0
        while time.time() < end and not self._stop_event.is_set():
            for i in range(self.num_leds):
                brightness = (math.sin((i / self.num_leds) * math.pi * 2 + t * speed) + 1) / 2
                self._set_pixel(i, int(color[0] * brightness),
                                int(color[1] * brightness), int(color[2] * brightness))
            self._show()
            t += 0.1
            time.sleep(0.02)

    def _pattern_solid(self, duration: float, color=(255, 255, 255), **_):
        self._set_all(*color)
        end = time.time() + duration
        while time.time() < end and not self._stop_event.is_set():
            time.sleep(0.1)

    def _flash_usb(self, duration: float):
        """Toggle USB power rapidly for a flash effect with simple strips."""
        end = time.time() + duration
        while time.time() < end:
            self._usb_power(True)
            time.sleep(0.3)
            self._usb_power(False)
            time.sleep(0.3)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _hsv_to_rgb(h: float, s: float, v: float) -> tuple:
        if s == 0.0:
            iv = int(v * 255)
            return iv, iv, iv
        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = int(255 * v * (1.0 - s))
        q = int(255 * v * (1.0 - s * f))
        t = int(255 * v * (1.0 - s * (1.0 - f)))
        iv = int(255 * v)
        i %= 6
        if i == 0: return iv, t, p
        if i == 1: return q, iv, p
        if i == 2: return p, iv, t
        if i == 3: return p, q, iv
        if i == 4: return t, p, iv
        return iv, p, q

    def cleanup(self):
        self._stop_current_animation()
        self._clear()
        logger.info("LED controller cleaned up")
