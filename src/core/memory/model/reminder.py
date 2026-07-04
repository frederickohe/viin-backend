from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base
from core.memory.model.memory_enums import ReminderStatus


class Reminder(Base):
    __tablename__ = "memory_reminders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Next scheduled fire time. If recurrence is present, this is the next occurrence.
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    timezone: Mapped[Optional[str]] = mapped_column(String(64))

    # Recurrence as an RFC5545 RRULE string (optional). Example: "FREQ=MONTHLY;BYMONTHDAY=15"
    rrule: Mapped[Optional[str]] = mapped_column(String(512))

    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus), nullable=False, default=ReminderStatus.SCHEDULED, index=True
    )

    # Connector delivery preferences (e.g., whatsapp|telegram|email|push)
    delivery: Mapped[dict] = mapped_column(JSON, nullable=False, default={})

    source_message_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("memory_source_messages.id"), index=True
    )

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


Index("ix_memory_reminders_owner_status_due", Reminder.owner_user_id, Reminder.status, Reminder.due_at)

