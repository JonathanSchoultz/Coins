"""Local audio playback module using OS audio tools."""

import logging
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Plays sound files on the locally attached speaker."""

    def __init__(self, config: dict, base_dir: str = "."):
        self.enabled = config.get("enabled", True)
        self.volume = config.get("volume", 0.8)
        self.sounds_dir = Path(base_dir) / config.get("sounds_dir", "sounds")
        self._process_lock = threading.Lock()
        self._active_processes = set()
        self._initialized = False

        if not self.enabled:
            logger.info("Audio player disabled in config")
            return

        self._paplay = shutil.which("paplay")
        self._aplay = shutil.which("aplay")
        self._mpg123 = shutil.which("mpg123")

        if not any((self._paplay, self._aplay, self._mpg123)):
            logger.error(
                "No audio backend found (need paplay/aplay/mpg123). Audio playback unavailable."
            )
            return

        self._initialized = True
        logger.info(
            "Audio player initialized (volume=%.1f, sounds_dir=%s, paplay=%s, aplay=%s, mpg123=%s)",
            self.volume,
            self.sounds_dir,
            bool(self._paplay),
            bool(self._aplay),
            bool(self._mpg123),
        )

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
        command = self._build_command(path)
        if not command:
            logger.error("No supported player found for file: %s", path)
            return

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            with self._process_lock:
                self._active_processes.add(proc)

            _, stderr = proc.communicate()
            if proc.returncode != 0:
                err = (stderr or "").strip()
                logger.error(
                    "Audio command failed (exit=%s): %s%s",
                    proc.returncode,
                    " ".join(command),
                    f" :: {err}" if err else "",
                )
        except Exception:
            logger.exception("Error playing sound: %s", path)
        finally:
            if "proc" in locals():
                with self._process_lock:
                    self._active_processes.discard(proc)

    def _build_command(self, path: Path):
        suffix = path.suffix.lower()
        path_str = str(path)

        # For PulseAudio/PipeWire setups, paplay is usually the most reliable.
        if suffix in {".wav", ".oga", ".ogg"}:
            if self._paplay:
                return [self._paplay, "--volume", str(self._paplay_volume()), path_str]
            if self._aplay and suffix == ".wav":
                return [self._aplay, path_str]
            return None

        if suffix == ".mp3":
            if self._mpg123:
                return [self._mpg123, "-q", "-f", str(self._mpg123_scale()), path_str]
            return None

        # Fallback: try paplay/aplay for unknown extensions.
        if self._paplay:
            return [self._paplay, "--volume", str(self._paplay_volume()), path_str]
        if self._aplay:
            return [self._aplay, path_str]
        return None

    def _paplay_volume(self) -> int:
        # PulseAudio volume is 0..65536.
        return int(max(0.0, min(1.0, self.volume)) * 65536)

    def _mpg123_scale(self) -> int:
        # mpg123 -f range is effectively 0..32768.
        return int(max(0.0, min(1.0, self.volume)) * 32768)

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
            sample_rate = 44100
            duration_ms = 300
            n_samples = int(sample_rate * duration_ms / 1000)
            silence_samples = int(sample_rate * 0.05)

            def tone(freq_hz: int):
                for i in range(n_samples):
                    sample = int(16000 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
                    yield struct.pack("<h", sample)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            try:
                with wave.open(str(tmp_path), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(b"".join(tone(440)))
                    wav_file.writeframes(b"\x00\x00" * silence_samples)
                    wav_file.writeframes(b"".join(tone(330)))

                self._play_blocking(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Error generating error beep")

    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))

    def stop(self):
        with self._process_lock:
            procs = list(self._active_processes)

        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def cleanup(self):
        self.stop()
        logger.info("Audio player cleaned up")
