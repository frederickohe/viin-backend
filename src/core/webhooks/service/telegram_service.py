import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

VIIN_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Start chatting with Viin"},
    {"command": "help", "description": "See what Viin can do"},
    {"command": "briefing", "description": "Today's tasks and overdue items"},
    {"command": "yesterday", "description": "What was due yesterday"},
    {"command": "addtask", "description": "Add a new task or reminder"},
]


class TelegramService:
    """Service for Telegram Bot API webhook setup and outbound messages."""

    def __init__(self):
        self.bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        self.webhook_url = (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
        self.webhook_secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def _post(self, method: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self.is_configured:
            logger.error("TELEGRAM_BOT_TOKEN is not configured")
            return None

        url = f"{self.api_base}/{method}"
        try:
            response = requests.post(url, json=payload or {}, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.error("Telegram API %s failed: %s", method, data)
                return None
            return data
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram API %s request failed: %s", method, exc)
            if hasattr(exc, "response") and exc.response is not None:
                logger.error("Response content: %s", exc.response.text)
            return None

    def send_message(self, chat_id: int | str, message_text: str) -> bool:
        """Send a text message to a Telegram chat."""
        text = (message_text or "").strip()
        if not text:
            logger.warning("Refusing to send empty Telegram message to %s", chat_id)
            return False

        result = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:4096],
            },
        )
        return result is not None

    def set_webhook(self, *, url: Optional[str] = None, secret_token: Optional[str] = None) -> bool:
        """Register the HTTPS webhook URL with Telegram."""
        webhook_url = (url or self.webhook_url).strip()
        if not webhook_url:
            logger.error("TELEGRAM_WEBHOOK_URL is not configured")
            return False

        payload: dict[str, Any] = {
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        }

        secret = (secret_token if secret_token is not None else self.webhook_secret).strip()
        if secret:
            payload["secret_token"] = secret

        result = self._post("setWebhook", payload)
        if result:
            logger.info("Telegram webhook registered at %s", webhook_url)
            return True
        return False

    def get_webhook_info(self) -> Optional[dict]:
        result = self._post("getWebhookInfo")
        if not result:
            return None
        return result.get("result")

    def delete_webhook(self) -> bool:
        result = self._post("deleteWebhook", {"drop_pending_updates": False})
        return result is not None

    def set_my_commands(self, commands: Optional[list[dict[str, str]]] = None) -> bool:
        """Register the bot command menu shown in Telegram."""
        payload = {"commands": commands or VIIN_BOT_COMMANDS}
        result = self._post("setMyCommands", payload)
        return result is not None
