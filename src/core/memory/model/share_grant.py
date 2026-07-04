from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base
from core.memory.model.memory_enums import MemoryVisibility


class MemoryShareGrant(Base):
    """
    One-to-one sharing control: "owner shares memory item with recipient".
    Visibility on MemoryItem is still PRIVATE/SHARED_1TO1, but this table is the actual ACL.
    """

    __tablename__ = "memory_share_grants"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    owner_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    recipient_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True, nullable=False)
    memory_item_id: Mapped[str] = mapped_column(String, ForeignKey("memory_items.id"), index=True, nullable=False)

    visibility: Mapped[MemoryVisibility] = mapped_column(Enum(MemoryVisibility), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


Index(
    "ix_memory_share_grants_owner_recipient_item",
    MemoryShareGrant.owner_user_id,
    MemoryShareGrant.recipient_user_id,
    MemoryShareGrant.memory_item_id,
)

