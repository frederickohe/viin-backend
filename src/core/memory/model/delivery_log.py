from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base
from core.memory.model.memory_enums import DeliveryStatus


class MemoryDeliveryLog(Base):
    __tablename__ = "memory_delivery_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    # What triggered the delivery (reminder or daily briefing etc.)
    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # reminder|daily_briefing|...
    reminder_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("memory_reminders.id"), index=True)

    subject: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default={})

    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus), nullable=False, default=DeliveryStatus.PENDING, index=True
    )
    external_message_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    error: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)


Index("ix_memory_delivery_logs_user_kind_created", MemoryDeliveryLog.user_id, MemoryDeliveryLog.kind, MemoryDeliveryLog.created_at)

