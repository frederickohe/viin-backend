from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base


class UserCreditBalance(Base):
    __tablename__ = "user_credit_balances"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "credit_type",
            "period_start",
            name="uq_user_credit_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(20), ForeignKey("users.id"), nullable=False)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("user_subscriptions.id"), nullable=True
    )
    credit_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allocated: Mapped[float] = mapped_column(Float, nullable=False)
    remaining: Mapped[float] = mapped_column(Float, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return (
            f"<UserCreditBalance(user_id={self.user_id}, "
            f"type={self.credit_type}, remaining={self.remaining})>"
        )
