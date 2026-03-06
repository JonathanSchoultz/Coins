"""Sonos speaker control module using the SoCo library."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import soco
    from soco.exceptions import SoCoException
    SOCO_AVAILABLE = True
except ImportError:
    SOCO_AVAILABLE = False
    logger.warning("soco not installed — Sonos control unavailable")


class SonosController:
    """Discovers and controls Sonos speakers on the local network."""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", True)
        self.default_volume = config.get("default_volume", 25)
        self._device: Optional[object] = None

        if not self.enabled:
            logger.info("Sonos controller disabled in config")
            return

        if not SOCO_AVAILABLE:
            return

        speaker_ip = config.get("speaker_ip")
        speaker_name = config.get("speaker_name")

        if speaker_ip:
            self._device = soco.SoCo(speaker_ip)
            logger.info("Sonos connected by IP: %s (%s)",
                        speaker_ip, self._device.player_name)
        else:
            self._discover(speaker_name)

    def _discover(self, name: Optional[str] = None):
        try:
            speakers = list(soco.discover(timeout=5) or [])
            if not speakers:
                logger.error("No Sonos speakers found on the network")
                return

            if name:
                for s in speakers:
                    if s.player_name.lower() == name.lower():
                        self._device = s
                        break
                if not self._device:
                    logger.error("Sonos speaker '%s' not found. Available: %s",
                                 name, [s.player_name for s in speakers])
                    return
            else:
                self._device = speakers[0]

            logger.info("Sonos discovered: %s (%s)",
                        self._device.player_name, self._device.ip_address)
        except Exception:
            logger.exception("Sonos discovery failed")

    @property
    def available(self) -> bool:
        return self._device is not None

    def play_uri(self, uri: str, title: str = "", volume: Optional[int] = None):
        """
        Play a URI on Sonos.
        Supports Sonos-compatible URIs: radio streams, Spotify, music library, etc.
        """
        if not self.available:
            logger.warning("Sonos not available — cannot play URI")
            return

        try:
            if volume is not None:
                self._device.volume = volume
            elif self._device.volume == 0:
                self._device.volume = self.default_volume

            meta = ""
            if title:
                meta = (
                    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
                    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                    '<item id="0" parentID="0" restricted="true">'
                    f'<dc:title>{title}</dc:title>'
                    '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                    '</item></DIDL-Lite>'
                )

            self._device.play_uri(uri, meta=meta, title=title)
            logger.info("Sonos playing: %s (volume=%s)", title or uri,
                        volume or self._device.volume)
        except Exception:
            logger.exception("Sonos play_uri failed: %s", uri)

    def play_favorite(self, name: str, volume: Optional[int] = None):
        """Play a Sonos favorite by name (partial match)."""
        if not self.available:
            logger.warning("Sonos not available — cannot play favorite")
            return

        try:
            favs = self._device.music_library.get_sonos_favorites()
            match = None
            for fav in favs:
                if name.lower() in fav.title.lower():
                    match = fav
                    break

            if not match:
                logger.error("Sonos favorite '%s' not found", name)
                return

            if volume is not None:
                self._device.volume = volume

            uri = match.get_uri()
            meta = match.resource_meta_data
            self._device.play_uri(uri, meta=meta)
            logger.info("Sonos playing favorite: %s", match.title)
        except Exception:
            logger.exception("Sonos play_favorite failed: %s", name)

    def play_queue(self, index: int = 0, volume: Optional[int] = None):
        """Play from the current queue at the given index."""
        if not self.available:
            return
        try:
            if volume is not None:
                self._device.volume = volume
            self._device.play_from_queue(index)
        except Exception:
            logger.exception("Sonos play_queue failed")

    def set_volume(self, volume: int):
        if self.available:
            self._device.volume = max(0, min(100, volume))

    def pause(self):
        if self.available:
            try:
                self._device.pause()
            except Exception:
                logger.exception("Sonos pause failed")

    def stop(self):
        if self.available:
            try:
                self._device.stop()
            except Exception:
                logger.exception("Sonos stop failed")

    def next_track(self):
        if self.available:
            try:
                self._device.next()
            except Exception:
                logger.exception("Sonos next failed")

    def previous_track(self):
        if self.available:
            try:
                self._device.previous()
            except Exception:
                logger.exception("Sonos previous failed")

    def group_all(self):
        """Group all speakers to play the same content."""
        if not self.available:
            return
        try:
            speakers = soco.discover(timeout=5)
            if speakers:
                for s in speakers:
                    if s != self._device:
                        s.join(self._device)
                logger.info("All Sonos speakers grouped")
        except Exception:
            logger.exception("Sonos grouping failed")

    def get_info(self) -> dict:
        """Return current playback info."""
        if not self.available:
            return {}
        try:
            info = self._device.get_current_transport_info()
            track = self._device.get_current_track_info()
            return {
                "state": info.get("current_transport_state"),
                "track": track.get("title"),
                "artist": track.get("artist"),
                "volume": self._device.volume,
                "speaker": self._device.player_name,
            }
        except Exception:
            logger.exception("Sonos get_info failed")
            return {}

    def cleanup(self):
        logger.info("Sonos controller cleaned up")
