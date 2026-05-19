from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base


class PostizOrganization(Base):
    """
    Mapping between an Autobus "client" (currently represented by `users.id`)
    and a Postiz organization + Public API key.
    """

    __tablename__ = "postiz_organizations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)

    user_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.id"), nullable=False, index=True
    )

    postiz_org_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    postiz_public_api_key_encrypted: Mapped[str] = mapped_column(String(1000), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_postiz_org_user"),
        UniqueConstraint("postiz_org_id", name="uq_postiz_org_id"),
    )

