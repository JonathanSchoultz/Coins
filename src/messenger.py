"""Messaging module supporting WhatsApp (via Twilio or Cloud API) and SMS."""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TWILIO_AVAILABLE = False
PYWA_AVAILABLE = False

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    pass

try:
    from pywa import WhatsApp
    PYWA_AVAILABLE = True
except ImportError:
    pass


def _resolve_env(value: str) -> str:
    """Resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return os.environ.get(env_key, "")
    return value


class Messenger:
    """Sends messages via WhatsApp or SMS."""

    def __init__(self, config: dict):
        self.enabled = config.get("enabled", True)
        self.provider = config.get("provider", "twilio")

        self._twilio_client = None
        self._twilio_from_sms = None
        self._twilio_from_wa = None
        self._whatsapp_cloud = None

        if not self.enabled:
            logger.info("Messenger disabled in config")
            return

        if self.provider == "twilio":
            self._init_twilio(config.get("twilio", {}))
        elif self.provider == "whatsapp_cloud":
            self._init_whatsapp_cloud(config.get("whatsapp_cloud", {}))
        else:
            logger.error("Unknown messaging provider: %s", self.provider)

    def _init_twilio(self, twilio_cfg: dict):
        if not TWILIO_AVAILABLE:
            logger.warning("twilio package not installed — messaging unavailable")
            return

        sid = _resolve_env(twilio_cfg.get("account_sid", ""))
        token = _resolve_env(twilio_cfg.get("auth_token", ""))
        self._twilio_from_sms = _resolve_env(twilio_cfg.get("from_number", ""))
        self._twilio_from_wa = _resolve_env(twilio_cfg.get("whatsapp_from", ""))

        if not sid or not token:
            logger.error("Twilio credentials not configured")
            return

        try:
            self._twilio_client = TwilioClient(sid, token)
            logger.info("Twilio messenger initialized")
        except Exception:
            logger.exception("Twilio initialization failed")

    def _init_whatsapp_cloud(self, wa_cfg: dict):
        if not PYWA_AVAILABLE:
            logger.warning("pywa package not installed — WhatsApp Cloud unavailable")
            return

        phone_id = _resolve_env(wa_cfg.get("phone_id", ""))
        token = _resolve_env(wa_cfg.get("token", ""))

        if not phone_id or not token:
            logger.error("WhatsApp Cloud credentials not configured")
            return

        try:
            self._whatsapp_cloud = WhatsApp(phone_id=phone_id, token=token)
            logger.info("WhatsApp Cloud messenger initialized")
        except Exception:
            logger.exception("WhatsApp Cloud initialization failed")

    def send(self, to: str, message: str, method: str = "whatsapp"):
        """
        Send a message.
        method: 'whatsapp', 'sms'
        """
        if not self.enabled:
            logger.warning("Messenger disabled — not sending to %s", to)
            return False

        logger.info("Sending %s message to %s", method, to)

        if method == "sms":
            return self._send_sms(to, message)
        elif method == "whatsapp":
            if self.provider == "twilio":
                return self._send_twilio_whatsapp(to, message)
            elif self.provider == "whatsapp_cloud":
                return self._send_whatsapp_cloud(to, message)
        else:
            logger.error("Unknown message method: %s", method)
            return False

    def _send_sms(self, to: str, message: str) -> bool:
        if not self._twilio_client:
            logger.error("Twilio not initialized — cannot send SMS")
            return False
        try:
            msg = self._twilio_client.messages.create(
                body=message,
                from_=self._twilio_from_sms,
                to=to,
            )
            logger.info("SMS sent: SID=%s", msg.sid)
            return True
        except Exception:
            logger.exception("SMS send failed")
            return False

    def _send_twilio_whatsapp(self, to: str, message: str) -> bool:
        if not self._twilio_client:
            logger.error("Twilio not initialized — cannot send WhatsApp")
            return False

        wa_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        try:
            msg = self._twilio_client.messages.create(
                body=message,
                from_=self._twilio_from_wa,
                to=wa_to,
            )
            logger.info("WhatsApp sent via Twilio: SID=%s", msg.sid)
            return True
        except Exception:
            logger.exception("WhatsApp send failed (Twilio)")
            return False

    def _send_whatsapp_cloud(self, to: str, message: str) -> bool:
        if not self._whatsapp_cloud:
            logger.error("WhatsApp Cloud not initialized — cannot send")
            return False
        try:
            to_clean = to.lstrip("+").replace("-", "").replace(" ", "")
            self._whatsapp_cloud.send_message(to=to_clean, text=message)
            logger.info("WhatsApp sent via Cloud API to %s", to)
            return True
        except Exception:
            logger.exception("WhatsApp send failed (Cloud API)")
            return False

    def cleanup(self):
        logger.info("Messenger cleaned up")
