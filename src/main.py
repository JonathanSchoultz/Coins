"""
Coin Reader — Main entry point.
Reads NFC tags, looks up their status, and executes configured actions.
"""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

import yaml

from .nfc_reader import NFCReader
from .led_controller import LEDController
from .audio_player import AudioPlayer
from .sonos_controller import SonosController
from .messenger import Messenger
from .coin_handler import CoinHandler

logger = logging.getLogger("coinreader")

BASE_DIR = Path(__file__).resolve().parent.parent


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.is_absolute():
        path = BASE_DIR / path

    if not path.exists():
        logger.error("Config file not found: %s", path)
        sys.exit(1)

    with open(path) as f:
        return yaml.safe_load(f) or {}


def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            handlers.append(logging.FileHandler(log_file))
        except PermissionError:
            pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main():
    parser = argparse.ArgumentParser(description="Coin Reader — NFC tag action system")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    parser.add_argument("-d", "--coins", default="coins.yaml", help="Path to coins database")
    parser.add_argument("--simulate", metavar="UID", help="Simulate a tag scan (for testing)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)

    logger.info("=" * 60)
    logger.info("Coin Reader starting")
    logger.info("=" * 60)

    led = LEDController(config.get("led", {}))
    audio = AudioPlayer(config.get("audio", {}), base_dir=str(BASE_DIR))
    sonos = SonosController(config.get("sonos", {}))
    messenger = Messenger(config.get("messaging", {}))

    coins_path = Path(args.coins)
    if not coins_path.is_absolute():
        coins_path = BASE_DIR / coins_path

    handler = CoinHandler(
        coins_file=str(coins_path),
        led=led,
        audio=audio,
        sonos=sonos,
        messenger=messenger,
        runtime_config=config.get("runtime", {}),
    )

    if args.simulate:
        logger.info("Simulation mode: tag UID = %s", args.simulate)
        handler.handle_tag(args.simulate)
        _cleanup(led, audio, sonos, messenger)
        return

    nfc = NFCReader(config.get("nfc", {}))

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received")
        nfc.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Ready — waiting for NFC tags...")
    try:
        nfc.poll_loop(on_tag=handler.handle_tag)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        _cleanup(led, audio, sonos, messenger, nfc)

    logger.info("Coin Reader stopped")


def _cleanup(*components):
    for c in components:
        try:
            c.cleanup()
        except Exception:
            logger.exception("Cleanup error for %s", type(c).__name__)


if __name__ == "__main__":
    main()
