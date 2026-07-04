from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base
from core.memory.model.memory_enums import MemoryItemType, MemoryVisibility


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    owner_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    visibility: Mapped[MemoryVisibility] = mapped_column(
        Enum(MemoryVisibility), nullable=False, default=MemoryVisibility.PRIVATE, index=True
    )

    item_type: Mapped[MemoryItemType] = mapped_column(Enum(MemoryItemType), nullable=False, index=True)

    title: Mapped[Optional[str]] = mapped_column(String(200))
    text: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(String(2048))

    # When this memory references an uploaded file/image in your storage service
    file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    tags: Mapped[dict] = mapped_column(JSON, nullable=False, default={})
    # SQLAlchemy declarative reserves attribute name "metadata".
    item_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default={})

    source_message_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("memory_source_messages.id"), index=True
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


Index("ix_memory_items_owner_type_created", MemoryItem.owner_user_id, MemoryItem.item_type, MemoryItem.created_at)

