from __future__ import annotations

import re
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from core.user.model.User import User
from core.user.service.user_service import UserService
from utilities.phone_utils import normalize_ghana_phone_number

_LINK_PHONE_RE = re.compile(
    r"^\s*link\s+(\+?\d[\d\s\-]{8,})\s*$",
    re.IGNORECASE,
)


def channel_type(user_id: str) -> str:
    uid = (user_id or "").strip()
    if uid.startswith("tg:"):
        return "telegram"
    if ":" in uid:
        return "merchant_scoped"
    return "phone"


def friendly_account_required_message(channel: str = "web") -> str:
    if channel == "whatsapp":
        return (
            "Hi! I don't have a Viin account for this number yet. "
            "Create your free account on the Viin website, verify your phone, "
            "then message me here again and I'll be ready to help."
        )
    if channel == "telegram":
        return (
            "Hi! To use tasks, reminders, and payments on Telegram, link the phone "
            "number on your Viin account by sending:\n"
            "link 0201234567\n\n"
            "Don't have an account yet? Sign up on the Viin website first, then come back and link your number."
        )
    return (
        "Hi! You'll need a Viin account for that. "
        "Sign up on the Viin website, verify your phone, sign in, and try again."
    )


def parse_link_phone_command(text: str) -> Optional[str]:
    match = _LINK_PHONE_RE.match(text or "")
    if not match:
        return None
    return normalize_ghana_phone_number(match.group(1))


def find_registered_user(
    db: Session,
    user_id: str,
    *,
    linked_phone: Optional[str] = None,
) -> Optional[User]:
    """Resolve a registered Viin user for a channel identifier (phone or Telegram)."""
    merchant_id, channel_user_id = _parse_merchant_scoped_user_id(user_id)
    user_service = UserService(db)

    if merchant_id:
        return db.query(User).filter(User.id == merchant_id).first()

    kind = channel_type(user_id)
    if kind == "telegram":
        phone = (linked_phone or "").strip()
        if not phone:
            return None
        return user_service.find_user_by_phone(phone)

    return user_service.find_user_by_phone(channel_user_id)


def resolve_session_user_id(
    user_id: str,
    *,
    linked_phone: Optional[str] = None,
) -> str:
    """
    Return the conversation/session key used for chat history.
    Telegram keeps tg:<chat_id>; phone channels use normalized phone.
    """
    if channel_type(user_id) == "telegram":
        return user_id
    merchant_id, channel_user_id = _parse_merchant_scoped_user_id(user_id)
    if merchant_id:
        return user_id
    return normalize_ghana_phone_number(channel_user_id) or channel_user_id


def _parse_merchant_scoped_user_id(user_id: str) -> Tuple[Optional[str], str]:
    if not user_id or ":" not in user_id:
        return None, user_id
    company_id, _, rest = user_id.partition(":")
    company_id = company_id.strip()
    rest = (rest or "").strip()
    if not company_id or not rest:
        return None, user_id
    return company_id, rest
