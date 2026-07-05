from __future__ import annotations

import logging

from core.nlu.service.process_message_result import ProcessMessageResult
from core.webhooks.service.telegram_service import TelegramService
from core.webhooks.service.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)


def send_whatsapp_nlu_response(
    whatsapp_service: WhatsAppService,
    *,
    phone_id: str,
    recipient_phone: str,
    result: ProcessMessageResult,
) -> bool:
    text_ok = whatsapp_service.send_message(
        phone_id=phone_id,
        recipient_phone=recipient_phone,
        message_text=result.text,
    )
    if not text_ok:
        return False

    if not result.audio_bytes:
        return True

    audio_ok = whatsapp_service.send_audio(
        phone_id=phone_id,
        recipient_phone=recipient_phone,
        audio_bytes=result.audio_bytes,
        mime_type=result.audio_mime_type,
    )
    if not audio_ok:
        logger.warning("Briefing text sent but WhatsApp audio failed for %s", recipient_phone)
    return text_ok


def send_telegram_nlu_response(
    telegram_service: TelegramService,
    *,
    chat_id: int | str,
    result: ProcessMessageResult,
    reply_markup: dict | None = None,
) -> bool:
    text_ok = telegram_service.send_message(
        chat_id,
        result.text,
        reply_markup=reply_markup,
    )
    if not text_ok:
        return False

    if not result.audio_bytes:
        return True

    audio_ok = telegram_service.send_audio(
        chat_id,
        result.audio_bytes,
        mime_type=result.audio_mime_type,
        caption="Your briefing",
    )
    if not audio_ok:
        logger.warning("Briefing text sent but Telegram audio failed for chat %s", chat_id)
    return text_ok
