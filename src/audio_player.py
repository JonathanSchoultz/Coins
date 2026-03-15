"""Local audio playback module using OS audio tools."""

import logging
import math
import os
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
        self.device = config.get("device")
        self.sounds_dir = Path(base_dir) / config.get("sounds_dir", "sounds")
        self._process_lock = threading.Lock()
        self._active_processes = set()
        self._initialized = False

        if not self.enabled:
            logger.info("Audio player disabled in config")
            return

        self._paplay = shutil.which("paplay")
        self._aplay = shutil.which("aplay")
        self._mpg321 = shutil.which("mpg321")
        self._mpg123 = shutil.which("mpg123")

        if not any((self._paplay, self._aplay, self._mpg321, self._mpg123)):
            logger.error(
                "No audio backend found (need paplay/aplay/mpg321/mpg123). Audio playback unavailable."
            )
            return

        self._initialized = True
        logger.info(
            "Audio player initialized (volume=%.1f, sounds_dir=%s, device=%s, paplay=%s, aplay=%s, mpg321=%s, mpg123=%s)",
            self.volume,
            self.sounds_dir,
            self.device or "default",
            bool(self._paplay),
            bool(self._aplay),
            bool(self._mpg321),
            bool(self._mpg123),
        )
        self._log_startup_audio_diagnostics()

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
        if path.suffix.lower() == ".mp3" and self._play_mp3_via_temp_wav(path):
            return

        commands = self._build_commands(path)
        if not commands:
            logger.error("No supported player found for file: %s", path)
            return

        last_error = ""
        for command in commands:
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
                if proc.returncode == 0:
                    return

                err = (stderr or "").strip()
                last_error = (
                    f"exit={proc.returncode} cmd={' '.join(command)}"
                    + (f" :: {err}" if err else "")
                )
                logger.warning("Audio command failed, trying fallback: %s", last_error)
            except Exception:
                logger.exception("Error playing sound with command: %s", " ".join(command))
                last_error = f"exception cmd={' '.join(command)}"
            finally:
                if "proc" in locals():
                    with self._process_lock:
                        self._active_processes.discard(proc)

        logger.error("All audio playback commands failed for %s: %s", path, last_error)

    def _play_mp3_via_temp_wav(self, path: Path) -> bool:
        """Decode MP3 to a temp WAV, then play via WAV backends.

        This avoids brittle MP3 output driver stacks (JACK/libao/out123) in
        headless systemd environments.
        """
        if not self._mpg123:
            return False

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            decode_cmd = [self._mpg123, "-q", "-w", str(tmp_path), str(path)]
            proc = subprocess.run(
                decode_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                logger.warning(
                    "MP3 decode-to-WAV failed (exit=%s): %s%s",
                    proc.returncode,
                    " ".join(decode_cmd),
                    f" :: {err}" if err else "",
                )
                return False

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                logger.warning("MP3 decode produced empty WAV: %s", tmp_path)
                return False

            # Reuse normal WAV playback path (paplay/aplay fallbacks).
            self._play_blocking(tmp_path)
            return True
        except Exception:
            logger.exception("Error decoding MP3 before playback: %s", path)
            return False
        finally:
            tmp_path.unlink(missing_ok=True)

    def _build_commands(self, path: Path):
        suffix = path.suffix.lower()
        path_str = str(path)
        commands = []

        # For systemd services without a user pulse daemon, ALSA is usually more reliable.
        if suffix in {".wav", ".oga", ".ogg"}:
            if self._paplay:
                cmd = [self._paplay]
                if self.device:
                    cmd.extend(["--device", str(self.device)])
                cmd.extend(["--volume", str(self._paplay_volume()), path_str])
                commands.append(cmd)
            if self._aplay and suffix == ".wav":
                cmd = [self._aplay]
                if self.device:
                    cmd.extend(["-D", str(self.device)])
                cmd.append(path_str)
                commands.append(cmd)
            return commands

        if suffix == ".mp3":
            if self._mpg321:
                commands.append([self._mpg321, "-q", path_str])
            if self._mpg123:
                commands.append(
                    [self._mpg123, "-q", "-o", "alsa", "-f", str(self._mpg123_scale()), path_str]
                )
                commands.append([self._mpg123, "-q", "-f", str(self._mpg123_scale()), path_str])
            return commands

        # Fallback: try paplay/aplay for unknown extensions.
        if self._paplay:
            cmd = [self._paplay]
            if self.device:
                cmd.extend(["--device", str(self.device)])
            cmd.extend(["--volume", str(self._paplay_volume()), path_str])
            commands.append(cmd)
        if self._aplay:
            cmd = [self._aplay]
            if self.device:
                cmd.extend(["-D", str(self.device)])
            cmd.append(path_str)
            commands.append(cmd)
        return commands

    def _paplay_volume(self) -> int:
        # PulseAudio volume is 0..65536.
        return int(max(0.0, min(1.0, self.volume)) * 65536)

    def _mpg123_scale(self) -> int:
        # mpg123 -f range is effectively 0..32768.
        return int(max(0.0, min(1.0, self.volume)) * 32768)

    def _log_startup_audio_diagnostics(self):
        """Emit startup diagnostics to make audio routing issues obvious."""
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        pulse_server = os.environ.get("PULSE_SERVER")
        logger.info(
            "Audio diagnostics: XDG_RUNTIME_DIR=%s PULSE_SERVER=%s",
            runtime_dir or "<unset>",
            pulse_server or "<unset>",
        )

        # If no explicit sink is configured, we cannot validate a target.
        if not self.device:
            return

        pactl = shutil.which("pactl")
        if not pactl:
            logger.warning("Audio diagnostics: pactl not found; cannot validate configured sink")
            return

        try:
            proc = subprocess.run(
                [pactl, "list", "short", "sinks"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                logger.warning(
                    "Audio diagnostics: failed to list sinks (exit=%s)%s",
                    proc.returncode,
                    f" :: {err}" if err else "",
                )
                return

            sinks = []
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    sinks.append(parts[1])

            if self.device in sinks:
                logger.info("Audio diagnostics: configured sink is available: %s", self.device)
            else:
                logger.warning(
                    "Audio diagnostics: configured sink not found: %s (available=%s)",
                    self.device,
                    ", ".join(sinks) if sinks else "<none>",
                )
        except subprocess.TimeoutExpired:
            logger.warning("Audio diagnostics: timeout while listing sinks")
        except Exception:
            logger.exception("Audio diagnostics failed unexpectedly")

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
