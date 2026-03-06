"""Local audio playback module using pygame.mixer."""

import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    logger.warning("pygame not installed — audio playback unavailable")


class AudioPlayer:
    """Plays sound files on the locally attached speaker."""

    def __init__(self, config: dict, base_dir: str = "."):
        self.enabled = config.get("enabled", True)
        self.volume = config.get("volume", 0.8)
        self.sounds_dir = Path(base_dir) / config.get("sounds_dir", "sounds")
        self._initialized = False

        if not self.enabled:
            logger.info("Audio player disabled in config")
            return

        if not PYGAME_AVAILABLE:
            return

        try:
            device = config.get("device")
            if device:
                os.environ["SDL_AUDIODRIVER"] = "alsa"
                os.environ["AUDIODEV"] = device

            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.music.set_volume(self.volume)
            self._initialized = True
            logger.info("Audio player initialized (volume=%.1f, sounds_dir=%s)",
                        self.volume, self.sounds_dir)
        except Exception:
            logger.exception("Failed to initialize audio")

    def play(self, filename: str, blocking: bool = False):
        """
        Play a sound file.
        If filename is an absolute path, use it directly.
        Otherwise, look in the configured sounds directory.
        """
        if not self._initialized:
            logger.warning("Audio not initialized — skipping playback of '%s'", filename)
            return

        path = Path(filename)
        if not path.is_absolute():
            path = self.sounds_dir / filename

        if not path.exists():
            logger.error("Sound file not found: %s", path)
            return

        logger.info("Playing sound: %s", path)

        if blocking:
            self._play_blocking(path)
        else:
            thread = threading.Thread(target=self._play_blocking, args=(path,), daemon=True)
            thread.start()

    def _play_blocking(self, path: Path):
        try:
            if path.suffix.lower() == ".wav":
                sound = pygame.mixer.Sound(str(path))
                sound.set_volume(self.volume)
                channel = sound.play()
                if channel:
                    while channel.get_busy():
                        pygame.time.wait(50)
            else:
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(50)
        except Exception:
            logger.exception("Error playing sound: %s", path)

    def play_error_sound(self):
        """Play the standard error/rejection sound."""
        error_file = self.sounds_dir / "error.wav"
        if error_file.exists():
            self.play("error.wav", blocking=False)
        else:
            self._play_generated_error()

    def _play_generated_error(self):
        """Generate a simple error beep if no error.wav exists."""
        if not self._initialized:
            return
        try:
            import array
            sample_rate = 44100
            duration_ms = 300
            freq = 440
            n_samples = int(sample_rate * duration_ms / 1000)
            import math
            buf = array.array("h", [
                int(16000 * math.sin(2 * math.pi * freq * i / sample_rate))
                for i in range(n_samples)
            ])
            sound = pygame.mixer.Sound(buffer=buf)
            sound.set_volume(self.volume)
            sound.play()
            pygame.time.wait(duration_ms + 50)
            # second beep (lower)
            buf2 = array.array("h", [
                int(16000 * math.sin(2 * math.pi * 330 * i / sample_rate))
                for i in range(n_samples)
            ])
            sound2 = pygame.mixer.Sound(buffer=buf2)
            sound2.set_volume(self.volume)
            sound2.play()
            pygame.time.wait(duration_ms + 50)
        except Exception:
            logger.exception("Error generating error beep")

    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))
        if self._initialized:
            pygame.mixer.music.set_volume(self.volume)

    def stop(self):
        if self._initialized:
            pygame.mixer.music.stop()
            pygame.mixer.stop()

    def cleanup(self):
        self.stop()
        if self._initialized:
            pygame.mixer.quit()
        logger.info("Audio player cleaned up")
