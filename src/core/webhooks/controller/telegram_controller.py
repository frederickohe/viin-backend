import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from core.nlu.nlu import AutobusNLUSystem
from core.nlu.service.account_access import (
    extract_phone_from_message,
    friendly_account_required_message,
    is_telegram_link_attempt,
    resolve_telegram_user,
    telegram_link_instruction_message,
    telegram_link_success_message,
)
from core.webhooks.service.telegram_service import TelegramService
from utilities.dbconfig import get_db
from utilities.phone_utils import normalize_ghana_phone_number

logger = logging.getLogger(__name__)

telegram_routes = APIRouter()


def _telegram_user_id(chat_id: int | str) -> str:
    return f"tg:{chat_id}"


_TELEGRAM_COMMAND_MESSAGES = {
    "/start": (
        "Hi! I'm Viin — your personal task assistant.\n\n"
        "First time here?\n"
        "1. Create your free account on the Viin website\n"
        "2. Verify your phone via OTP\n"
        "3. Connect this chat by sending:\n"
        "link 0247291736\n"
        "(use your actual Viin phone number)\n\n"
        "Tap the menu button (☰) to see commands, or try:\n"
        "• /briefing — today's tasks\n"
        "• /addtask — add a task\n"
        "• /help — full list of capabilities"
    ),
    "/help": (
        "Here's what I can help with:\n\n"
        "📋 Tasks & reminders\n"
        "• Add tasks with or without a due date\n"
        "• Daily or weekly briefings\n"
        "• Check what was due yesterday\n\n"
        "🔗 Connect your account\n"
        "• Send: link 0247291736 (use the phone on your Viin account)\n\n"
        "💬 General help\n"
        "• Answer questions about your pending to-dos\n\n"
        "Try: \"remind me to call John tomorrow at 3pm\" or \"what do I need to do today?\""
    ),
    "/link": (
        "To connect this Telegram chat to your Viin account, send:\n"
        "link 0247291736\n\n"
        "Replace 0247291736 with the phone number on your Viin account."
    ),
    "/briefing": "What do I need to do today? Give me my daily briefing.",
    "/tasks": "What do I need to do today? Give me my daily briefing.",
    "/yesterday": "Was there something I needed to do yesterday?",
    "/missed": "Was there something I needed to do yesterday?",
    "/addtask": "I want to add a new task.",
}


def _verify_telegram_secret(
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
) -> None:
    expected = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return
    if x_telegram_bot_api_secret_token != expected:
        logger.warning("Telegram webhook rejected: invalid secret token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )


@telegram_routes.post("/telegram")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_telegram_secret),
):
    """
    Receives Telegram Bot API updates (set via setWebhook).
    """
    payload = await request.json()
    logger.info("Received Telegram update: %s", json.dumps(payload, indent=2))

    telegram_service = TelegramService()
    if not telegram_service.is_configured:
        logger.error("Telegram webhook received but TELEGRAM_BOT_TOKEN is missing")
        return {"ok": True}

    if "callback_query" in payload:
        return await _handle_callback_query(payload["callback_query"], telegram_service, db)

    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        logger.warning("Telegram message missing chat.id")
        return {"ok": True}

    contact = message.get("contact")
    if contact and contact.get("phone_number"):
        return await _handle_phone_link(
            chat_id=chat_id,
            phone=contact.get("phone_number"),
            telegram_service=telegram_service,
            db=db,
        )

    text = (message.get("text") or "").strip()
    if not text:
        telegram_service.send_message(
            chat_id,
            "I can read text messages for now. Send me a message and I'll help you out.",
        )
        return {"ok": True}

    command_key = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command_key in _TELEGRAM_COMMAND_MESSAGES:
        text = _TELEGRAM_COMMAND_MESSAGES[command_key]

    return await _handle_text_message(
        chat_id=chat_id,
        text=text,
        telegram_service=telegram_service,
        db=db,
        guest_ok=command_key in ("/start", "/help", "/link"),
    )


async def _handle_phone_link(
    *,
    chat_id: int | str,
    phone: str,
    telegram_service: TelegramService,
    db: Session,
) -> dict:
    nlu_user_id = _telegram_user_id(chat_id)
    nlu_system = AutobusNLUSystem(db_session=db)
    registered = resolve_telegram_user(
        db,
        nlu_user_id,
        f"link {phone}",
        conversation_manager=nlu_system.conversation_manager,
    )
    if not registered:
        normalized = normalize_ghana_phone_number(phone)
        telegram_service.send_message(
            chat_id,
            friendly_account_required_message("telegram", phone=normalized),
        )
        return {"ok": True}

    telegram_service.send_message(
        chat_id,
        telegram_link_success_message(registered),
    )
    return {"ok": True}


async def _handle_text_message(
    *,
    chat_id: int | str,
    text: str,
    telegram_service: TelegramService,
    db: Session,
    guest_ok: bool = False,
) -> dict:
    try:
        nlu_user_id = _telegram_user_id(chat_id)
        nlu_system = AutobusNLUSystem(db_session=db)
        link_attempt = is_telegram_link_attempt(text)
        link_phone = extract_phone_from_message(text)
        registered = resolve_telegram_user(
            db,
            nlu_user_id,
            text,
            conversation_manager=nlu_system.conversation_manager,
        )

        if link_attempt and registered:
            response_message = telegram_link_success_message(registered)
        elif link_attempt and not registered:
            response_message = friendly_account_required_message(
                "telegram", phone=link_phone
            )
        elif not registered and not guest_ok:
            response_message = telegram_link_instruction_message()
        else:
            if registered:
                nlu_system.set_telegram_context_user(registered)
            response_message = nlu_system.process_message(nlu_user_id, text)

        logger.info("Generated Telegram response for %s", nlu_user_id)

        if not telegram_service.send_message(chat_id, response_message):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send Telegram message",
            )

        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error handling Telegram message: %s", exc, exc_info=True)
        telegram_service.send_message(
            chat_id,
            "Something went wrong on my end. Please try again in a moment.",
        )
        return {"ok": True}


async def _handle_callback_query(
    callback_query: dict,
    telegram_service: TelegramService,
    db: Session,
) -> dict:
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    data = (callback_query.get("data") or "").strip()

    if chat_id is None:
        return {"ok": True}

    if data:
        return await _handle_text_message(
            chat_id=chat_id,
            text=data,
            telegram_service=telegram_service,
            db=db,
        )

    return {"ok": True}
