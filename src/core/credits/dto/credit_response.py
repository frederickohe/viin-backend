from typing import Dict, Optional

from pydantic import BaseModel


class CreditBalanceItem(BaseModel):
    credit_type: str
    label: str
    allocated: float
    remaining: float
    used: float
    period_start: str
    period_end: str


class UserCreditsResponse(BaseModel):
    user_id: str
    plan_id: Optional[int] = None
    plan_name: str
    has_active_subscription: bool
    credits: Dict[str, CreditBalanceItem]
