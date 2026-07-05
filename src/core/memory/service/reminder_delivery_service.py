from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from core.memory.model.reminder import Reminder
from core.nlu.service.conversation_manager import ConversationManager
from core.user.model.User import User
from core.webhooks.service.telegram_service import TelegramService
from core.webhooks.service.whatsapp_service import WhatsAppService
from core.wirepick.service.wirepickservice import WirepickSMSService, WirepickSMSException
from config import settings

logger = logging.getLogger(__name__)

_FREQ_STEP = {
    "DAILY": lambda dt: dt + timedelta(days=1),
    "WEEKLY": lambda dt: dt + timedelta(weeks=1),
    "MONTHLY": lambda dt: dt + timedelta(days=30),
}


class ReminderDeliveryService:
    """Deliver due reminders to chat, SMS (Wirepick), WhatsApp, or Telegram."""

    def __init__(self) -> None:
        self.conversation_manager = ConversationManager()
        self.sms_service = WirepickSMSService()
        self.sms_enabled = getattr(settings, "SMS_NOTIFICATION_ENABLED", True)

    @staticmethod
    def build_message(reminder: Reminder) -> str:
        title = (reminder.title or "").strip()
        body = (reminder.body or "").strip()
        if title and title != body:
            return f"Reminder: {title}\n{body}"
        return f"Reminder: {body}"

    @staticmethod
    def build_sms_message(reminder: Reminder) -> str:
        text = ReminderDeliveryService.build_message(reminder)
        return text[:160]

    @staticmethod
    def default_delivery_for_owner(owner_user_id: str) -> dict:
        owner = (owner_user_id or "").strip()
        if owner.startswith("tg:"):
            chat_id = owner.removeprefix("tg:").strip()
            delivery: dict = {"channels": ["telegram", "sms"]}
            if chat_id:
                delivery["telegram_chat_id"] = chat_id
            return delivery
        return {"channels": ["chat", "sms"]}

    @staticmethod
    def resolve_channels(reminder: Reminder, user: Optional[User]) -> List[str]:
        delivery = reminder.delivery or {}
        channels = delivery.get("channels")
        if isinstance(channels, list) and channels:
            return [str(c).lower() for c in channels]

        legacy = (delivery.get("channel") or "").strip().lower()
        if legacy:
            return [legacy]

        owner_id = (reminder.owner_user_id or "").strip()
        if owner_id.startswith("tg:"):
            return ["telegram"]
        if user and (os.getenv("WHATSAPP_phone_ID") or "").strip():
            return ["whatsapp", "sms"]
        return ["chat", "sms"]

    @staticmethod
    def conversation_user_id(user: User, owner_user_id: str) -> Optional[str]:
        if user:
            return (getattr(user, "phone", None) or "").strip() or None
        if owner_user_id.startswith("tg:"):
            return owner_user_id
        return owner_user_id or None

    def deliver_chat(
        self,
        *,
        conversation_user_id: str,
        message: str,
        reminder_id: str,
    ) -> Tuple[bool, Optional[str]]:
        try:
            chat_text = f"⏰ {message}"
            state = self.conversation_manager.get_conversation_state(conversation_user_id)
            state.conversation_history.append(
                {
                    "role": "assistant",
                    "content": chat_text,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "reminder",
                    "reminder_id": reminder_id,
                }
            )
            if len(state.conversation_history) > 50:
                state.conversation_history = state.conversation_history[-50:]
            self.conversation_manager._save_conversation_state(state)
            return True, None
        except Exception as exc:
            logger.error("[REMINDER_CHAT] failed user=%s err=%s", conversation_user_id, exc, exc_info=True)
            return False, str(exc)

    def deliver_sms(self, *, phone: str, message: str) -> Tuple[bool, Optional[str]]:
        if not self.sms_enabled:
            return False, "SMS notifications disabled"
        if not phone:
            return False, "Missing recipient phone"
        try:
            result = self.sms_service.send_sms(phone, message)
            if result.get("success"):
                return True, None
            return False, result.get("error") or "SMS send failed"
        except WirepickSMSException as exc:
            return False, str(exc)

    def deliver_whatsapp(self, *, user: User, message: str) -> Tuple[bool, Optional[str]]:
        phone_id = (os.getenv("WHATSAPP_phone_ID") or "").strip()
        recipient = (getattr(user, "whatsapp_number", None) or getattr(user, "phone", None) or "").strip()
        if not phone_id or not recipient:
            return False, "Missing WHATSAPP_phone_ID or recipient phone"
        ok = WhatsAppService().send_message(
            phone_id=phone_id,
            recipient_phone=recipient,
            message_text=f"⏰ {message}",
        )
        return (ok, None if ok else "WhatsApp send_message failed")

    @staticmethod
    def resolve_telegram_chat_id(
        owner_user_id: str,
        user: Optional[User],
        delivery: Optional[dict] = None,
    ) -> Optional[str]:
        """Resolve Telegram chat id from delivery payload, tg: owner id, or linked user agents."""
        payload = delivery or {}
        stored = payload.get("telegram_chat_id")
        if stored is not None:
            chat_id = str(stored).strip()
            if chat_id:
                return chat_id

        owner = (owner_user_id or "").strip()
        if owner.startswith("tg:"):
            chat_id = owner.removeprefix("tg:").strip()
            if chat_id:
                return chat_id

        if user:
            agents = user.agents or {}
            linked = (agents.get("telegram") or {}).get("chat_id")
            if linked is not None:
                chat_id = str(linked).strip()
                if chat_id:
                    return chat_id

        return None

    def deliver_telegram(
        self,
        *,
        owner_user_id: str,
        message: str,
        user: Optional[User] = None,
        delivery: Optional[dict] = None,
    ) -> Tuple[bool, Optional[str]]:
        chat_id = self.resolve_telegram_chat_id(owner_user_id, user, delivery)
        if not chat_id:
            return False, "No Telegram chat id linked for this user"
        ok = TelegramService().send_message(chat_id=chat_id, message_text=f"⏰ {message}")
        return (ok, None if ok else "Telegram send_message failed")

    def deliver(
        self,
        db: Session,
        reminder: Reminder,
        user: Optional[User],
    ) -> Tuple[bool, List[dict]]:
        """
        Attempt delivery on all configured channels.
        Returns (any_success, per_channel_results).
        """
        message = self.build_message(reminder)
        sms_message = self.build_sms_message(reminder)
        channels = self.resolve_channels(reminder, user)
        conv_user_id = self.conversation_user_id(user, reminder.owner_user_id or "")
        results: List[dict] = []

        for channel in channels:
            ok = False
            err: Optional[str] = None

            if channel == "chat":
                if not conv_user_id:
                    err = "No conversation user id for chat delivery"
                else:
                    ok, err = self.deliver_chat(
                        conversation_user_id=conv_user_id,
                        message=message,
                        reminder_id=reminder.id,
                    )
            elif channel == "sms":
                phone = (getattr(user, "phone", None) or "").strip() if user else ""
                ok, err = self.deliver_sms(phone=phone, message=sms_message)
            elif channel == "whatsapp":
                if not user:
                    err = "No user record for WhatsApp delivery"
                else:
                    ok, err = self.deliver_whatsapp(user=user, message=message)
            elif channel == "telegram":
                ok, err = self.deliver_telegram(
                    owner_user_id=reminder.owner_user_id or "",
                    message=message,
                    user=user,
                    delivery=reminder.delivery or {},
                )
            else:
                err = f"Unsupported channel: {channel}"

            results.append({"channel": channel, "ok": ok, "error": err})

        any_success = any(r["ok"] for r in results)
        return any_success, results

    @staticmethod
    def advance_recurrence(reminder: Reminder) -> Optional[datetime]:
        rrule = (reminder.rrule or "").strip().upper()
        if not rrule:
            return None
        match = re.search(r"FREQ=(\w+)", rrule)
        if not match:
            return None
        step = _FREQ_STEP.get(match.group(1))
        if not step:
            return None
        due = reminder.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return step(due)
