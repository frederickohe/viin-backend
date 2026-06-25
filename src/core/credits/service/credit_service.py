import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from core.credits.model.credit_types import (
    ALL_CREDIT_TYPES,
    CREDIT_TYPE_LABELS,
    PLAN_CREDIT_DEFAULTS,
    CreditType,
)
from core.credits.model.credit_usage_log import CreditUsageLog
from core.credits.model.user_credit_balance import UserCreditBalance
from core.subscription.model.subscription_plan import SubscriptionPlan
from core.subscription.model.user_subscription import UserSubscription
from core.subscription.service.subscription_service import SubscriptionService
from core.user.model.User import User

logger = logging.getLogger(__name__)


class CreditService:
    def __init__(self, db: Session):
        self.db = db

    def resolve_user_id(self, identifier: Optional[str]) -> Optional[str]:
        """Resolve internal user id from id, email, or phone."""
        if not identifier:
            return None
        user = (
            self.db.query(User)
            .filter(
                (User.id == identifier)
                | (User.email == identifier)
                | (User.phone == identifier)
            )
            .first()
        )
        return user.id if user else None

    def _ensure_internal_user_id(self, identifier: str) -> str:
        """Map JWT subject / phone / id to users.id before writing credit rows."""
        user_id = self.resolve_user_id(identifier)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return user_id

    @staticmethod
    def _normalize_plan_key(plan_name: str) -> str:
        return (plan_name or "").strip().lower()

    def get_plan_allocations(self, plan: SubscriptionPlan) -> Dict[str, float]:
        """Resolve credit allocations from plan JSON column or name-based defaults."""
        from_json = plan.get_credit_allocations() if hasattr(plan, "get_credit_allocations") else {}
        if from_json:
            return {k: float(v) for k, v in from_json.items()}

        key = self._normalize_plan_key(plan.name)
        defaults = PLAN_CREDIT_DEFAULTS.get(key)
        if defaults:
            return dict(defaults)

        return dict(PLAN_CREDIT_DEFAULTS["free"])

    def sync_plan_credit_allocations(self) -> int:
        """Persist default allocations onto existing plans (matched by name)."""
        updated = 0
        plans = self.db.query(SubscriptionPlan).all()
        for plan in plans:
            key = self._normalize_plan_key(plan.name)
            if key not in PLAN_CREDIT_DEFAULTS:
                continue
            allocations = PLAN_CREDIT_DEFAULTS[key]
            plan.credit_allocations = json.dumps(allocations)
            plan.updated_at = datetime.now(timezone.utc)
            updated += 1
        if updated:
            self.db.commit()
        return updated

    def _get_or_create_balances(
        self, user_id: str, subscription: Optional[UserSubscription] = None
    ) -> List[UserCreditBalance]:
        """Ensure the user has balance rows for the current billing period."""
        user_id = self._ensure_internal_user_id(user_id)
        sub = subscription or SubscriptionService(self.db).get_user_active_subscription(user_id)
        now = datetime.now(timezone.utc)

        if sub and sub.is_active:
            period_start = sub.started_at
            period_end = sub.expires_at
            allocations = self.get_plan_allocations(sub.plan)
            subscription_id = sub.id
        else:
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if period_start.month == 12:
                period_end = period_start.replace(year=period_start.year + 1, month=1)
            else:
                period_end = period_start.replace(month=period_start.month + 1)
            allocations = dict(PLAN_CREDIT_DEFAULTS["free"])
            subscription_id = None

        existing = (
            self.db.query(UserCreditBalance)
            .filter(
                UserCreditBalance.user_id == user_id,
                UserCreditBalance.period_start == period_start,
            )
            .all()
        )
        if existing:
            return existing

        balances: List[UserCreditBalance] = []
        for credit_type in ALL_CREDIT_TYPES:
            allocated = float(allocations.get(credit_type, 0))
            balance = UserCreditBalance(
                user_id=user_id,
                subscription_id=subscription_id,
                credit_type=credit_type,
                allocated=allocated,
                remaining=allocated,
                period_start=period_start,
                period_end=period_end,
            )
            self.db.add(balance)
            balances.append(balance)

        self.db.commit()
        for b in balances:
            self.db.refresh(b)
        return balances

    def initialize_credits_for_subscription(self, user_id: str, subscription: UserSubscription) -> None:
        """Reset credit balances when a user subscribes or upgrades."""
        user_id = self._ensure_internal_user_id(user_id)
        self.db.query(UserCreditBalance).filter(
            UserCreditBalance.user_id == user_id
        ).delete(synchronize_session=False)
        self.db.commit()
        self._get_or_create_balances(user_id, subscription)

    def get_user_credits(self, user_id: str) -> Dict[str, Any]:
        """Full credit snapshot for API responses."""
        sub_service = SubscriptionService(self.db)
        subscription = sub_service.get_user_active_subscription(user_id)
        balances = self._get_or_create_balances(user_id, subscription)

        credits: Dict[str, Dict[str, Any]] = {}
        for balance in balances:
            used = max(0.0, balance.allocated - balance.remaining)
            credits[balance.credit_type] = {
                "credit_type": balance.credit_type,
                "label": CREDIT_TYPE_LABELS.get(balance.credit_type, balance.credit_type),
                "allocated": balance.allocated,
                "remaining": balance.remaining,
                "used": used,
                "period_start": balance.period_start.isoformat(),
                "period_end": balance.period_end.isoformat(),
            }

        plan_name = None
        plan_id = None
        if subscription:
            plan_name = subscription.plan.name
            plan_id = subscription.plan.id

        return {
            "user_id": user_id,
            "plan_id": plan_id,
            "plan_name": plan_name or "Free",
            "has_active_subscription": subscription is not None,
            "credits": credits,
        }

    def get_remaining(self, user_id: str, credit_type: str) -> float:
        balances = self._get_or_create_balances(user_id)
        for balance in balances:
            if balance.credit_type == credit_type:
                return balance.remaining
        return 0.0

    def has_credits(self, user_id: str, credit_type: str, amount: float = 1.0) -> bool:
        return self.get_remaining(user_id, credit_type) >= amount

    def check_and_deduct(
        self,
        user_id: str,
        credit_type: str,
        amount: float = 1.0,
        operation: str = "usage",
        metadata: Optional[Dict[str, Any]] = None,
        raise_on_insufficient: bool = True,
    ) -> bool:
        """Atomically deduct credits. Returns True if successful."""
        balances = self._get_or_create_balances(user_id)
        target = next((b for b in balances if b.credit_type == credit_type), None)

        if target is None or target.remaining < amount:
            if raise_on_insufficient:
                label = CREDIT_TYPE_LABELS.get(credit_type, credit_type)
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "message": f"Insufficient {label} credits. Please upgrade your plan.",
                        "credit_type": credit_type,
                        "remaining": target.remaining if target else 0,
                        "required": amount,
                    },
                )
            return False

        target.remaining = max(0.0, target.remaining - amount)
        target.updated_at = datetime.now(timezone.utc)

        log = CreditUsageLog(
            user_id=user_id,
            credit_type=credit_type,
            amount=amount,
            operation=operation,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        self.db.add(log)
        self.db.commit()
        return True

    def require_credits(
        self,
        user_id: str,
        credit_type: str,
        amount: float = 1.0,
        operation: str = "usage",
    ) -> None:
        """Raise HTTP 402 if the user cannot afford the operation."""
        self.check_and_deduct(
            user_id=user_id,
            credit_type=credit_type,
            amount=amount,
            operation=operation,
            raise_on_insufficient=True,
        )
