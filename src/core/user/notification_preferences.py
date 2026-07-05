"""Helpers for enforcing user notification channel preferences."""

from __future__ import annotations

from typing import Iterable, List, Optional

from core.user.model.User import User

CHAT_CHANNELS = frozenset({"chat", "whatsapp", "telegram"})
SMS_CHANNELS = frozenset({"sms"})


def allows_in_app_notifications(user: Optional[User]) -> bool:
    """In-app/chat is allowed unless the user explicitly opted out."""
    if user is None:
        return True
    return user.in_app_notification is not False


def allows_sms_notifications(user: Optional[User]) -> bool:
    """SMS requires an explicit opt-in on the user profile."""
    if user is None:
        return False
    return bool(user.sms_notification)


def channel_allowed(channel: str, user: Optional[User]) -> bool:
    normalized = (channel or "").strip().lower()
    if normalized in SMS_CHANNELS:
        return allows_sms_notifications(user)
    if normalized in CHAT_CHANNELS:
        return allows_in_app_notifications(user)
    return True


def filter_channels_by_user_prefs(
    channels: Iterable[str],
    user: Optional[User],
) -> List[str]:
    return [ch for ch in channels if channel_allowed(ch, user)]
