import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from core.nlu.nlu import AutobusNLUSystem
from core.webhooks.service.telegram_service import TelegramService
from utilities.dbconfig import get_db

logger = logging.getLogger(__name__)

telegram_routes = APIRouter()


def _telegram_user_id(chat_id: int | str) -> str:
    return f"tg:{chat_id}"


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

    text = (message.get("text") or "").strip()
    if not text:
        telegram_service.send_message(
            chat_id,
            "I can read text messages for now. Send me a message and I'll help you out.",
        )
        return {"ok": True}

    return await _handle_text_message(
        chat_id=chat_id,
        text=text,
        telegram_service=telegram_service,
        db=db,
    )


async def _handle_text_message(
    *,
    chat_id: int | str,
    text: str,
    telegram_service: TelegramService,
    db: Session,
) -> dict:
    try:
        nlu_user_id = _telegram_user_id(chat_id)
        nlu_system = AutobusNLUSystem(db_session=db)
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
            "An error occurred while processing your message. Please try again.",
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
