"""Coin action orchestrator — loads coin database and executes actions."""

import logging
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yaml

from .led_controller import LEDController
from .audio_player import AudioPlayer
from .sonos_controller import SonosController
from .messenger import Messenger

logger = logging.getLogger(__name__)


class CoinStatus:
    READY = "ready"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    REDEEMED = "redeemed"


class CoinHandler:
    """Loads coin definitions and orchestrates action execution."""

    def __init__(
        self,
        coins_file: str,
        led: LEDController,
        audio: AudioPlayer,
        sonos: SonosController,
        messenger: Messenger,
        runtime_config: Optional[dict] = None,
    ):
        self.led = led
        self.audio = audio
        self.sonos = sonos
        self.messenger = messenger
        self._coins: dict = {}
        self._coins_by_uid: dict = {}
        self._coins_file = Path(coins_file)
        self._runtime = runtime_config or {}
        self._one_time_only = self._runtime.get("one_time_only", True)
        self._coin_sound = self._runtime.get("coin_sound", "")
        self._coin_sound_blocking = self._runtime.get("coin_sound_blocking", True)
        self._success_sound = self._runtime.get("success_sound", "reward.wav")
        self._redeemed_state_file = Path(
            self._runtime.get("redeemed_state_file", "redeemed_tags.yaml")
        )
        self._redeemed: set[str] = set()
        self._silenced_redeemed_uids: set[str] = set()
        self._load_redeemed_state()
        self._load_coins()

    @staticmethod
    def _normalize_uid(uid: str) -> str:
        """
        Normalize UID for stable matching/persistence.

        Accepts forms like:
          - 04b0aaa21f1d90
          - 04:B0:AA:A2:1F:1D:90
          - 04 b0 aa a2 1f 1d 90
        Returns:
          - 04:B0:AA:A2:1F:1D:90
        """
        compact = "".join(ch for ch in str(uid).upper() if ch in "0123456789ABCDEF")
        if not compact:
            return str(uid).strip().upper()
        if len(compact) % 2 == 1:
            # Keep odd-length formats as upper raw to avoid accidental corruption.
            return str(uid).strip().upper()
        return ":".join(compact[i:i + 2] for i in range(0, len(compact), 2))

    def _load_redeemed_state(self):
        if not self._redeemed_state_file.exists():
            return
        try:
            with open(self._redeemed_state_file) as f:
                data = yaml.safe_load(f) or {}
            redeemed = data.get("redeemed_uids", [])
            self._redeemed = set(self._normalize_uid(x) for x in redeemed)
            logger.info("Loaded %d redeemed UID(s) from %s",
                        len(self._redeemed), self._redeemed_state_file)
        except Exception:
            logger.exception("Failed to load redeemed state file")

    def _save_redeemed_state(self):
        try:
            payload = {
                "redeemed_uids": sorted(self._redeemed),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            with open(self._redeemed_state_file, "w") as f:
                yaml.safe_dump(payload, f, sort_keys=False)
        except Exception:
            logger.exception("Failed to save redeemed state file")

    def _load_coins(self):
        if not self._coins_file.exists():
            logger.error("Coins file not found: %s", self._coins_file)
            return

        try:
            with open(self._coins_file) as f:
                data = yaml.safe_load(f) or {}
            raw_coins = data.get("coins", {})

            # Support two formats:
            # 1) mapping (preferred):
            #    coins:
            #      "04...": {name: "...", actions: [...]}
            # 2) list:
            #    coins:
            #      - uid: "04..."
            #        name: "..."
            #        actions: [...]
            if isinstance(raw_coins, dict):
                self._coins = raw_coins
            elif isinstance(raw_coins, list):
                mapped: dict = {}
                for row in raw_coins:
                    if not isinstance(row, dict):
                        continue
                    uid = row.get("uid")
                    if not uid:
                        continue
                    entry = dict(row)
                    entry.pop("uid", None)
                    mapped[str(uid)] = entry
                self._coins = mapped
            else:
                logger.error("Invalid coins format in %s: expected mapping or list",
                             self._coins_file)
                self._coins = {}

            self._coins_by_uid = {
                self._normalize_uid(uid): coin for uid, coin in self._coins.items()
            }
            logger.info("Loaded %d coin definitions from %s",
                        len(self._coins), self._coins_file)
        except Exception:
            logger.exception("Failed to load coins file")

    def reload(self):
        """Hot-reload coin definitions without restarting."""
        logger.info("Reloading coin database...")
        self._load_coins()

    def get_status(self, uid: str) -> str:
        norm_uid = self._normalize_uid(uid)
        coin = self._coins_by_uid.get(norm_uid)
        if coin is None:
            return CoinStatus.UNKNOWN

        if self._one_time_only and norm_uid in self._redeemed:
            return CoinStatus.REDEEMED

        expires = coin.get("expires")
        if expires is None:
            return CoinStatus.READY

        if isinstance(expires, str):
            try:
                expires = datetime.fromisoformat(expires)
            except ValueError:
                logger.error("Invalid expiry date for %s: %s", uid, expires)
                return CoinStatus.READY
        elif isinstance(expires, date):
            expires = datetime.combine(expires, datetime.min.time())

        if expires < datetime.now():
            return CoinStatus.EXPIRED

        return CoinStatus.READY

    def handle_tag(self, uid: str):
        """Main entry point: process a scanned NFC tag."""
        norm_uid = self._normalize_uid(uid)

        # Hard guard: redeemed tags are always ignored silently, even if the
        # coin entry is later missing/changed in coins.yaml.
        if self._one_time_only and norm_uid in self._redeemed:
            if norm_uid not in self._silenced_redeemed_uids:
                logger.info("Ignoring already redeemed UID silently: %s", norm_uid)
                self._silenced_redeemed_uids.add(norm_uid)
            return

        status = self.get_status(norm_uid)
        coin = self._coins_by_uid.get(norm_uid, {})
        name = coin.get("name", norm_uid)

        logger.info("Processing coin '%s' (raw=%s, normalized=%s) — status: %s",
                    name, uid, norm_uid, status)

        if status == CoinStatus.UNKNOWN:
            logger.warning("Unknown tag: %s", norm_uid)
            self._handle_rejection("Unknown tag")
            return

        if status == CoinStatus.EXPIRED:
            logger.warning("Expired coin: %s (%s)", name, norm_uid)
            self._handle_rejection(f"Expired: {name}")
            return

        if status == CoinStatus.REDEEMED:
            # Intentionally silent for already redeemed tags.
            # A redeemed coin may remain on the reader; do nothing to avoid
            # repeated sounds/LED flashes.
            if norm_uid not in self._silenced_redeemed_uids:
                logger.info("Ignoring already redeemed coin: %s (%s)", name, norm_uid)
                self._silenced_redeemed_uids.add(norm_uid)
            return

        actions = coin.get("actions", [])
        if not actions:
            logger.info("Coin '%s' has no actions defined", name)
            return

        if self._coin_sound:
            self.audio.play(self._coin_sound, blocking=self._coin_sound_blocking)

        if self._success_sound:
            self.audio.play(self._success_sound, blocking=False)

        logger.info("Executing %d action(s) for '%s'", len(actions), name)
        had_action_failure = False
        for i, action in enumerate(actions):
            try:
                self._execute_action(action, i + 1, len(actions))
            except Exception:
                had_action_failure = True
                logger.exception("Action %d failed for coin '%s'", i + 1, name)

        # Mark as redeemed after we have attempted the first valid execution path.
        # This avoids "redeemed before action" behavior when debugging playback.
        if self._one_time_only and not had_action_failure:
            self._redeemed.add(norm_uid)
            self._save_redeemed_state()
            logger.info("Marked coin as redeemed: %s (%s)", name, norm_uid)

    def _handle_rejection(self, reason: str):
        """Handle rejected tags without any audio interruption."""
        logger.info("Rejection: %s", reason)
        self.led.flash_error(duration=3.0)

    def _execute_action(self, action: dict, index: int, total: int):
        action_type = action.get("type")
        logger.info("  Action %d/%d: %s", index, total, action_type)

        if action_type == "led_pattern":
            self.led.play_pattern(
                pattern=action.get("pattern", "solid"),
                duration=action.get("duration", 10),
                color=action.get("color"),
                speed=action.get("speed", 1.0),
            )

        elif action_type == "play_sound":
            self.audio.play(
                filename=action.get("file", ""),
                blocking=action.get("blocking", False),
            )

        elif action_type == "sonos_play":
            uri = action.get("uri")
            favorite = action.get("favorite")
            volume = action.get("volume")

            if favorite:
                self.sonos.play_favorite(favorite, volume=volume)
            elif uri:
                self.sonos.play_uri(uri, title=action.get("title", ""), volume=volume)
            else:
                logger.warning("sonos_play action missing 'uri' or 'favorite'")

        elif action_type == "sonos_control":
            command = action.get("command")
            if command == "pause":
                self.sonos.pause()
            elif command == "stop":
                self.sonos.stop()
            elif command == "next":
                self.sonos.next_track()
            elif command == "previous":
                self.sonos.previous_track()
            elif command == "volume":
                self.sonos.set_volume(action.get("level", 25))
            elif command == "group_all":
                self.sonos.group_all()
            else:
                logger.warning("Unknown sonos_control command: %s", command)

        elif action_type == "send_message":
            self.messenger.send(
                to=action.get("to", ""),
                message=action.get("message", ""),
                method=action.get("method", "whatsapp"),
            )

        elif action_type == "wait":
            seconds = action.get("seconds", 1)
            logger.info("  Waiting %.1f seconds", seconds)
            time.sleep(seconds)

        else:
            logger.warning("Unknown action type: %s", action_type)
