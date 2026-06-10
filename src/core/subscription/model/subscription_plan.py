from sqlalchemy import String, DateTime, Integer, Float, Boolean, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from utilities.dbconfig import Base
from typing import Optional, List
import enum
import json


class BillingPeriod(str, enum.Enum):
    MONTHLY = "monthly"
    ANNUALLY = "annually"


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)  # e.g., "Basic", "Premium", "Pro"
    price: Mapped[float] = mapped_column(Float, nullable=False)  # Price per billing period
    billing_period: Mapped[BillingPeriod] = mapped_column(Enum(BillingPeriod), nullable=False, default=BillingPeriod.MONTHLY)
    billing_period_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # e.g., 3 months, 6 months
    features: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string of features
    agents: Mapped[str] = mapped_column(Text, nullable=False, default='[]')  # JSON string list of agent identifiers
    credit_allocations: Mapped[Optional[str]] = mapped_column(Text)  # JSON: per-resource monthly credits
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self):
        return f"<SubscriptionPlan(id={self.id}, name={self.name}, price={self.price}, period={self.billing_period})>"
    
    def get_period_description(self) -> str:
        """Get a human-readable description of the billing period"""
        if self.billing_period_count == 1:
            return self.billing_period.value
        else:
            period_map = {
                BillingPeriod.MONTHLY: "month",
                BillingPeriod.ANNUALLY: "year"
            }
            base_period = period_map.get(self.billing_period, self.billing_period.value)
            return f"{self.billing_period_count} {base_period}s"
    
    def get_features_list(self) -> List[str]:
        """Parse and return features as a list of strings"""
        try:
            if isinstance(self.features, list):
                return self.features
            return json.loads(self.features) if self.features else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_agents_list(self) -> List[str]:
        """Parse and return agents as a list of agent identifiers"""
        try:
            if isinstance(self.agents, list):
                return self.agents
            return json.loads(self.agents) if self.agents else []
        except (json.JSONDecodeError, TypeError):
            return []

    def get_credit_allocations(self) -> dict:
        """Parse and return credit allocations as a dict."""
        try:
            if isinstance(self.credit_allocations, dict):
                return self.credit_allocations
            if self.credit_allocations:
                return json.loads(self.credit_allocations)
        except (json.JSONDecodeError, TypeError):
            pass
        return {}