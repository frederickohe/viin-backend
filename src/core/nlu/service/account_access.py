from __future__ import annotations

import logging
import re
from typing import Optional, Tuple, TYPE_CHECKING

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import object_session

from config import settings
from core.user.model.User import User
from core.user.service.user_service import UserService
from utilities.phone_utils import (
    convert_to_local_ghana_format,
    normalize_ghana_phone_number,
)

if TYPE_CHECKING:
    from core.nlu.service.conversation_manager import ConversationManager

_LINK_PHONE_RE = re.compile(
    r"^\s*link\s+(\+?\d[\d\s\-]{8,})\s*$",
    re.IGNORECASE,
)

_TELEGRAM_AGENT = "telegram"

logger = logging.getLogger(__name__)


def signup_url() -> str:
    return f"{settings.BASE_FRONTEND_URL.rstrip('/')}/signup"


def channel_type(user_id: str) -> str:
    uid = (user_id or "").strip()
    if uid.startswith("tg:"):
        return "telegram"
    if ":" in uid:
        return "merchant_scoped"
    return "phone"


def telegram_link_instruction_message() -> str:
    return (
        "This Telegram chat isn't connected to your Viin account yet.\n\n"
        "To connect, send your Viin phone number exactly like this:\n"
        "link 0247291736\n\n"
        "(Replace with the phone number on your account.)\n\n"
        f"New here? Create a free account at {signup_url()}, verify your phone via OTP, "
        "then send the link command above."
    )


def friendly_account_required_message(
    channel: str = "web",
    *,
    phone: Optional[str] = None,
) -> str:
    signup = signup_url()
    if channel == "whatsapp":
        return (
            "Hi! I don't have a Viin account for this number yet. "
            f"Create your free account at {signup}, verify your phone, "
            "then message me here again and I'll be ready to help."
        )
    if channel == "telegram":
        if phone:
            display = convert_to_local_ghana_format(phone) or phone
            return (
                f"I couldn't find a Viin account for {display}.\n\n"
                "Please check the number matches your Viin account exactly, "
                "including the leading 0 (e.g. link 0247291736).\n\n"
                f"If you haven't signed up yet, create your account at {signup} "
                "and verify your phone via OTP first."
            )
        return telegram_link_instruction_message()
    return (
        "Hi! You'll need a Viin account for that. "
        f"Create one at {signup}, verify your phone, sign in, and try again."
    )


def parse_link_phone_command(text: str) -> Optional[str]:
    match = _LINK_PHONE_RE.match(text or "")
    if not match:
        return None
    return normalize_ghana_phone_number(match.group(1))


def is_telegram_link_attempt(text: str) -> bool:
    return parse_link_phone_command(text) is not None


def extract_phone_from_message(text: str) -> Optional[str]:
    """Return a normalized phone from an explicit link command."""
    return parse_link_phone_command(text)


def telegram_chat_id_from_user_id(user_id: str) -> Optional[str]:
    uid = (user_id or "").strip()
    if not uid.startswith("tg:"):
        return None
    chat_id = uid.removeprefix("tg:").strip()
    return chat_id or None


def _sync_telegram_link_state(
    conversation_manager: Optional["ConversationManager"],
    user_id: str,
    user: User,
) -> None:
    if conversation_manager is None:
        return
    state = conversation_manager.get_conversation_state(user_id)
    state.viin_linked_phone = user.phone
    state.viin_linked_user_id = str(user.id)
    conversation_manager._save_conversation_state(state)


def find_user_by_telegram_chat_id(db: Session, chat_id: str) -> Optional[User]:
    if not chat_id:
        return None
    chat_id = str(chat_id).strip()

    try:
        user = (
            db.query(User)
            .filter(User.agents[_TELEGRAM_AGENT]["chat_id"].astext == chat_id)
            .first()
        )
        if user:
            return user
    except Exception:
        logger.debug("JSONB telegram chat lookup failed; falling back to scan", exc_info=True)

    rows = db.query(User).filter(User.agents.isnot(None)).all()
    for user in rows:
        stored = (user.agents or {}).get(_TELEGRAM_AGENT, {}).get("chat_id")
        if stored is not None and str(stored) == chat_id:
            return user
    return None


def bind_telegram_chat(db: Session, user: User, chat_id: str) -> None:
    agents = dict(user.agents or {})
    agents[_TELEGRAM_AGENT] = {"chat_id": str(chat_id)}
    user.agents = agents
    if object_session(user) is not None:
        flag_modified(user, "agents")
    db.add(user)
    db.commit()
    db.refresh(user)


def get_telegram_chat_id(user: User) -> Optional[str]:
    stored = (user.agents or {}).get(_TELEGRAM_AGENT, {}).get("chat_id")
    if stored is None:
        return None
    chat_id = str(stored).strip()
    return chat_id or None


def _clear_telegram_link_state(
    conversation_manager: Optional["ConversationManager"],
    chat_id: str,
) -> None:
    if conversation_manager is None or not chat_id:
        return
    nlu_user_id = f"tg:{chat_id}"
    state = conversation_manager.get_conversation_state(nlu_user_id)
    state.viin_linked_phone = None
    state.viin_linked_user_id = None
    conversation_manager._save_conversation_state(state)


def unbind_telegram_chat(
    db: Session,
    user: User,
    *,
    conversation_manager: Optional["ConversationManager"] = None,
) -> bool:
    chat_id = get_telegram_chat_id(user)
    if not chat_id:
        return False

    user.remove_agent(_TELEGRAM_AGENT)
    db.add(user)
    db.commit()
    db.refresh(user)
    _clear_telegram_link_state(conversation_manager, chat_id)
    return True


def telegram_unlink_success_message() -> str:
    return (
        "This Telegram chat is no longer connected to your Viin account.\n\n"
        "To use Viin here again, send:\n"
        "link 0247291736\n"
        "(use the phone number on your Viin account)"
    )


def telegram_not_linked_message() -> str:
    return (
        "This chat isn't connected to a Viin account right now.\n\n"
        "To connect, send:\n"
        "link 0247291736\n"
        "(use the phone number on your Viin account)"
    )


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
        _sync_telegram_link_state(conversation_manager, user_id, user)
        return user

    linked_phone = None
    linked_user_id = None
    if conversation_manager is not None:
        state = conversation_manager.get_conversation_state(user_id)
        linked_phone = getattr(state, "viin_linked_phone", None)
        linked_user_id = getattr(state, "viin_linked_user_id", None)

    if linked_user_id:
        user = db.query(User).filter(User.id == linked_user_id).first()
        if user:
            _sync_telegram_link_state(conversation_manager, user_id, user)
            return user

    phone_hint = (linked_phone or "").strip() or extract_phone_from_message(message_text or "")
    if not phone_hint:
        return None

    user = UserService(db).find_user_by_phone(phone_hint)
    if not user:
        logger.info(
            "Telegram link failed for chat %s: no Viin user for phone hint %s",
            chat_id,
            phone_hint,
        )
        return None

    try:
        bind_telegram_chat(db, user, chat_id)
    except Exception:
        logger.exception("Failed to bind Telegram chat %s to user %s", chat_id, user.id)
        return None
    if conversation_manager is not None:
        _sync_telegram_link_state(conversation_manager, user_id, user)
    return user


def find_registered_user(
    db: Session,
    user_id: str,
    *,
    linked_phone: Optional[str] = None,
    linked_user_id: Optional[str] = None,
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
        if linked_user_id:
            user = db.query(User).filter(User.id == linked_user_id).first()
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
