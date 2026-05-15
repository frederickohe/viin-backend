from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from utilities.dbconfig import Base


class DigitalMarketingPostAsset(Base):
    """
    Archived marketing caption + media URLs after a successful Postiz publish
    (digital marketing agent flow).
    """

    __tablename__ = "digital_marketing_post_assets"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)

    user_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.id"), nullable=False, index=True
    )

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    marketing_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    content_links: Mapped[List[str]] = mapped_column(JSON, nullable=False)

    postiz_response: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
