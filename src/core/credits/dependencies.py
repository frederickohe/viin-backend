"""FastAPI helpers for credit enforcement at endpoint boundaries."""

from fastapi import Depends, HTTPException, status
from another_fastapi_jwt_auth import AuthJWT
from sqlalchemy.orm import Session

from core.credits.model.credit_types import CreditType
from core.credits.service.credit_service import CreditService
from core.user.controller.usercontroller import validate_token, get_db
from core.user.model.User import User
from core.user.service.user_service import UserService


def require_credit(
    credit_type: str,
    amount: float = 1.0,
    operation: str = "api_call",
):
    """Dependency factory: authenticate user and deduct credits before the handler runs."""

    def _dependency(
        authjwt: AuthJWT = Depends(validate_token),
        db: Session = Depends(get_db),
    ) -> User:
        user_service = UserService(db)
        user = user_service.get_current_user(authjwt.get_jwt_subject())
        CreditService(db).require_credits(
            user_id=user.id,
            credit_type=credit_type,
            amount=amount,
            operation=operation,
        )
        return user

    return _dependency


def require_llm_credit(operation: str = "llm_chat"):
    return require_credit(CreditType.LLM.value, 1.0, operation)


def require_image_gen_credit(operation: str = "image_generation"):
    return require_credit(CreditType.IMAGE_GEN.value, 1.0, operation)


def require_video_gen_credit(operation: str = "video_generation"):
    return require_credit(CreditType.VIDEO_GEN.value, 1.0, operation)


def require_email_credit(operation: str = "email_send"):
    return require_credit(CreditType.EMAIL.value, 1.0, operation)


def require_sms_credit(operation: str = "sms_send"):
    return require_credit(CreditType.SMS.value, 1.0, operation)


def require_storage_credit(mb: float, operation: str = "storage_upload"):
    return require_credit(CreditType.STORAGE_MB.value, mb, operation)


def require_server_credit(operation: str = "server_request"):
    return require_credit(CreditType.SERVER.value, 1.0, operation)
