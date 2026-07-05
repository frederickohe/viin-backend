import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

VIIN_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Start chatting with Viin"},
    {"command": "help", "description": "See what Viin can do"},
    {"command": "link", "description": "Connect your Viin phone number"},
    {"command": "unlink", "description": "Disconnect this chat from Viin"},
    {"command": "briefing", "description": "Today's tasks and overdue items"},
    {"command": "weekly", "description": "Tasks and reminders for this week"},
    {"command": "monthly", "description": "Overview for the rest of this month"},
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

    def send_message(
        self,
        chat_id: int | str,
        message_text: str,
        *,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Send a text message to a Telegram chat."""
        text = (message_text or "").strip()
        if not text:
            logger.warning("Refusing to send empty Telegram message to %s", chat_id)
            return False

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        result = self._post("sendMessage", payload)
        return result is not None

    def send_audio(
        self,
        chat_id: int | str,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/mpeg",
        caption: Optional[str] = None,
        filename: str = "briefing.mp3",
    ) -> bool:
        """Send an audio file to a Telegram chat."""
        if not audio_bytes:
            logger.warning("Refusing to send empty Telegram audio to %s", chat_id)
            return False
        if not self.is_configured:
            logger.error("TELEGRAM_BOT_TOKEN is not configured")
            return False

        url = f"{self.api_base}/sendAudio"
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        files = {"audio": (filename, audio_bytes, mime_type)}

        try:
            response = requests.post(url, data=data, files=files, timeout=60)
            response.raise_for_status()
            payload_json = response.json()
            if not payload_json.get("ok"):
                logger.error("Telegram sendAudio failed: %s", payload_json)
                return False
            return True
        except requests.exceptions.RequestException as exc:
            logger.error("Telegram sendAudio request failed: %s", exc)
            if hasattr(exc, "response") and exc.response is not None:
                logger.error("Response content: %s", exc.response.text)
            return False

    @staticmethod
    def build_due_date_keyboard(*, now: Optional[datetime] = None) -> dict[str, Any]:
        """Inline keyboard with the next 14 days plus common weekday shortcuts."""
        now = now or datetime.now(timezone.utc)
        today = now.date()
        rows: list[list[dict[str, str]]] = []

        quick_row: list[dict[str, str]] = []
        for offset, label in ((0, "Today"), (1, "Tomorrow"), (7, "Next week")):
            day = today + timedelta(days=offset)
            quick_row.append(
                {
                    "text": label,
                    "callback_data": f"due:{day.isoformat()}",
                }
            )
        rows.append(quick_row)

        day_buttons: list[dict[str, str]] = []
        for offset in range(2, 14):
            day = today + timedelta(days=offset)
            if offset == 7:
                continue
            day_buttons.append(
                {
                    "text": day.strftime("%a %d %b"),
                    "callback_data": f"due:{day.isoformat()}",
                }
            )
        for index in range(0, len(day_buttons), 3):
            rows.append(day_buttons[index : index + 3])

        weekday_row = []
        for name, weekday in (
            ("Mon", 0),
            ("Tue", 1),
            ("Wed", 2),
            ("Thu", 3),
            ("Fri", 4),
            ("Sat", 5),
            ("Sun", 6),
        ):
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            day = today + timedelta(days=days_ahead)
            weekday_row.append(
                {
                    "text": f"This {name}",
                    "callback_data": f"due:{day.isoformat()}",
                }
            )
        for index in range(0, len(weekday_row), 4):
            rows.append(weekday_row[index : index + 4])

        return {"inline_keyboard": rows}

    def answer_callback_query(self, callback_query_id: str) -> bool:
        result = self._post(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id},
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
