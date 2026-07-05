from __future__ import annotations

import re
from typing import Optional, Tuple, TYPE_CHECKING

from sqlalchemy.orm import Session

from core.user.model.User import User
from core.user.service.user_service import UserService
from utilities.phone_utils import (
    convert_to_local_ghana_format,
    extract_ghana_phone_numbers_from_text,
    normalize_ghana_phone_number,
)

if TYPE_CHECKING:
    from core.nlu.service.conversation_manager import ConversationManager

_LINK_PHONE_RE = re.compile(
    r"^\s*link\s+(\+?\d[\d\s\-]{8,})\s*$",
    re.IGNORECASE,
)

_TELEGRAM_AGENT = "telegram"


def channel_type(user_id: str) -> str:
    uid = (user_id or "").strip()
    if uid.startswith("tg:"):
        return "telegram"
    if ":" in uid:
        return "merchant_scoped"
    return "phone"


def friendly_account_required_message(
    channel: str = "web",
    *,
    phone: Optional[str] = None,
) -> str:
    if channel == "whatsapp":
        return (
            "Hi! I don't have a Viin account for this number yet. "
            "Create your free account on the Viin website, verify your phone, "
            "then message me here again and I'll be ready to help."
        )
    if channel == "telegram":
        if phone:
            display = convert_to_local_ghana_format(phone) or phone
            return (
                f"I couldn't find a Viin account for {display}. "
                "Sign up on the Viin website with this number, verify it, then message me again."
            )
        return (
            "Hi! I don't have a Viin account for this chat yet. "
            "Sign up on the Viin website with your phone number, verify it, then message me here."
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


def extract_phone_from_message(text: str) -> Optional[str]:
    """Return a normalized phone when the user shares one (with or without a link command)."""
    linked = parse_link_phone_command(text)
    if linked:
        return linked

    stripped = (text or "").strip()
    if stripped and re.fullmatch(r"[\d\s+\-()]+", stripped):
        digits = re.sub(r"\D", "", stripped)
        if 9 <= len(digits) <= 13:
            return normalize_ghana_phone_number(stripped)

    phones = extract_ghana_phone_numbers_from_text(text or "")
    if len(phones) == 1:
        return normalize_ghana_phone_number(phones[0])
    return None


def telegram_chat_id_from_user_id(user_id: str) -> Optional[str]:
    uid = (user_id or "").strip()
    if not uid.startswith("tg:"):
        return None
    chat_id = uid.removeprefix("tg:").strip()
    return chat_id or None


def find_user_by_telegram_chat_id(db: Session, chat_id: str) -> Optional[User]:
    if not chat_id:
        return None
    chat_id = str(chat_id).strip()
    rows = db.query(User).filter(User.agents.isnot(None)).all()
    for user in rows:
        stored = (user.agents or {}).get(_TELEGRAM_AGENT, {}).get("chat_id")
        if stored and str(stored) == chat_id:
            return user
    return None


def bind_telegram_chat(db: Session, user: User, chat_id: str) -> None:
    user.set_agent(_TELEGRAM_AGENT, {"chat_id": str(chat_id)})
    db.add(user)
    db.commit()
    db.refresh(user)


def telegram_link_success_message(user: User) -> str:
    first_name = (user.fullname or "").split()[0] if user.fullname else "there"
    display_phone = convert_to_local_ghana_format(user.phone or "") or user.phone or "your account"
    return (
        f"You're all set, {first_name}! "
        f"I've connected this chat to {display_phone}. What can I help you with?"
    )


def resolve_telegram_user(
    db: Session,
    user_id: str,
    message_text: Optional[str] = None,
    *,
    conversation_manager: Optional["ConversationManager"] = None,
) -> Optional[User]:
    """
    Match a Telegram chat to a Viin account by stored chat id, session phone, or phone in the message.
    Persists the chat id on the user when a match is found.
    """
    if channel_type(user_id) != "telegram":
        return None

    chat_id = telegram_chat_id_from_user_id(user_id)
    if not chat_id:
        return None

    user = find_user_by_telegram_chat_id(db, chat_id)
    if user:
        return user

    linked_phone = None
    if conversation_manager is not None:
        state = conversation_manager.get_conversation_state(user_id)
        linked_phone = getattr(state, "viin_linked_phone", None)

    phone_hint = (linked_phone or "").strip() or extract_phone_from_message(message_text or "")
    if not phone_hint:
        return None

    user = UserService(db).find_user_by_phone(phone_hint)
    if not user:
        return None

    bind_telegram_chat(db, user, chat_id)
    if conversation_manager is not None:
        state = conversation_manager.get_conversation_state(user_id)
        state.viin_linked_phone = user.phone
        conversation_manager._save_conversation_state(state)
    return user


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
        chat_id = telegram_chat_id_from_user_id(user_id)
        if chat_id:
            user = find_user_by_telegram_chat_id(db, chat_id)
            if user:
                return user
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
