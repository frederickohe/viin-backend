from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base


class SourceMessage(Base):
    """
    Raw inbound content from connectors (WhatsApp/Telegram/email/app upload).
    This table is intentionally flexible; normalized objects (memory items, lists, reminders)
    should reference a source_message_id when created from an inbound message.
    """

    __tablename__ = "memory_source_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    channel: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # whatsapp|telegram|email|app
    external_message_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    text: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default={})

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


Index(
    "ix_memory_source_messages_user_channel_created",
    SourceMessage.user_id,
    SourceMessage.channel,
    SourceMessage.created_at,
)

