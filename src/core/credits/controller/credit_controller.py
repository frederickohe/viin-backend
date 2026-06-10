from fastapi import APIRouter, Depends
from another_fastapi_jwt_auth import AuthJWT
from sqlalchemy.orm import Session

from core.credits.dto.credit_response import CreditBalanceItem, UserCreditsResponse
from core.credits.service.credit_service import CreditService
from core.user.controller.usercontroller import validate_token, get_db
from core.user.service.user_service import UserService

credit_routes = APIRouter()


@credit_routes.get("/me", response_model=UserCreditsResponse)
def get_my_credits(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """Current user's credit balances per resource category."""
    user = UserService(db).get_current_user(authjwt.get_jwt_subject())
    data = CreditService(db).get_user_credits(user.id)
    credits = {
        key: CreditBalanceItem(**value) for key, value in data["credits"].items()
    }
    return UserCreditsResponse(
        user_id=data["user_id"],
        plan_id=data.get("plan_id"),
        plan_name=data.get("plan_name") or "Free",
        has_active_subscription=data.get("has_active_subscription", False),
        credits=credits,
    )
