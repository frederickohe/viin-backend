from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from utilities.dbconfig import Base


class ChatwootAccount(Base):
    """
    Mapping between an Autobus user (`users.id`) and a Chatwoot tenant.

    We provision:
    - a Chatwoot Account (tenant)
    - a Chatwoot User
    - an AccountUser role binding
    """

    __tablename__ = "chatwoot_accounts"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)

    user_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.id"), nullable=False, index=True
    )

    chatwoot_account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chatwoot_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Chatwoot returns an access_token on user creation via platform API.
    # Store encrypted when TOKEN_ENCRYPTION_KEY is configured.
    chatwoot_user_access_token_encrypted: Mapped[str] = mapped_column(
        String(2000), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_chatwoot_user"),
        UniqueConstraint("chatwoot_account_id", name="uq_chatwoot_account_id"),
        UniqueConstraint("chatwoot_user_id", name="uq_chatwoot_user_id"),
    )

