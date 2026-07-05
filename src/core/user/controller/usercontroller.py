from fastapi import APIRouter, Depends, HTTPException, status, Query
from datetime import datetime
from typing import List, Optional
import jwt
from pydantic import BaseModel
from core.auth.service.sessiondriver import SessionDriver, TokenData
from another_fastapi_jwt_auth import AuthJWT
from core.exceptions import *
from core.user.dto.response.paged_users import PagedUserResponse
from utilities.dbconfig import SessionLocal
from sqlalchemy.orm import Session
from core.user.model.User import User
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# DTO Models
from core.user.dto.response.message_response import MessageResponse
from core.user.dto.response.user_response import UserResponse
from core.user.dto.response.sent_emails_response import SentEmailsResponse, SentEmailItem

from core.user.service.user_service import UserService
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from core.user.dto.request.user_update_request import UserUpdateRequest
from core.user.dto.request.notification_settings_update_request import (
    NotificationSettingsUpdateRequest,
)
from core.user.dto.request.profile_image_update_request import ProfileImageUpdateRequest
from core.user.dto.request.agent_update_request import AgentUpdateRequest
from core.user.dto.response.user_agents_response import UserAgentsResponse
from core.agent.agent_params import AGENT_REQUIRED_PARAMS
from core.agent.tools.agent_config.user_agent_config_service import AgentConfigService
from core.notification.service.notification_service import NotificationService
from core.notification.dto.response.paged_notifications import PagedNotificationResponse
from core.notification.model.Notification import NotificationStatus, NotificationType
from core.histories.service.historyservice import HistoryService
from core.histories.dto.response.historyresponse import HistoryResponseDTO
from core.receipts.service.receipt_service import ReceiptService
from core.receipts.dto.response.receiptresponse import ReceiptResponse
from core.agent.tools.email.email import EmailTool

def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401, 
            detail="Token expired. Please log in again."
        )
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}"
        )
    
# Controller (Router)
user_routes = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@user_routes.get("/me", response_model=UserResponse)
def get_current_user_endpoint(authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    # Get the current user's email/subject from the JWT
    current_user_email = authjwt.get_jwt_subject()
    
    user_service = UserService(db)
    
    # Use the email to get the user
    return user_service.get_current_user(current_user_email)


@user_routes.get("/me/emails/sent", response_model=SentEmailsResponse)
def list_my_sent_emails(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=50),
):
    """
    Recent emails sent through Autobus EmailTool (ZeptoMail), keyed by your account phone.

    History is populated when a send succeeds from the NLU/agent flow; max 100 retained in Redis.
    """
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    phone = (user.phone or "").strip()
    if not phone:
        return SentEmailsResponse(emails=[], total_returned=0)

    tool = EmailTool(db_pool=db)
    rows = tool.list_sent_emails_for_user(phone, limit=limit)
    items: List[SentEmailItem] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        items.append(
            SentEmailItem(
                to=str(r.get("to") or ""),
                subject=str(r.get("subject") or ""),
                sent_at=str(r.get("sent_at") or ""),
            )
        )
    return SentEmailsResponse(emails=items, total_returned=len(items))


@user_routes.get("/me/notifications", response_model=PagedNotificationResponse)
def get_my_notifications(
    authjwt: AuthJWT = Depends(validate_token),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1),
    status: Optional[str] = None,
    type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    notif_service = NotificationService(db)

    status_enum = None
    type_enum = None
    try:
        if status:
            status_enum = NotificationStatus(status)
        if type:
            type_enum = NotificationType(type)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid status or type value")

    return notif_service.get_user_notifications_paged(user.id, page, size, status_enum, type_enum)


@user_routes.get("/{user_id}/notifications", response_model=PagedNotificationResponse)
def get_user_notifications(
    user_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1),
    status: Optional[str] = None,
    type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    notif_service = NotificationService(db)

    status_enum = None
    type_enum = None
    try:
        if status:
            status_enum = NotificationStatus(status)
        if type:
            type_enum = NotificationType(type)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid status or type value")

    return notif_service.get_user_notifications_paged(user_id, page, size, status_enum, type_enum)


@user_routes.get("/me/financials", response_model=List[HistoryResponseDTO])
def get_my_financials(
    authjwt: AuthJWT = Depends(validate_token),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    intent: Optional[str] = None,
    transaction_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    history_service = HistoryService(db)
    return history_service.get_user_histories(user.id, page, page_size, intent, transaction_type, start_date, end_date)


@user_routes.get("/{user_id}/financials", response_model=List[HistoryResponseDTO])
def get_user_financials(
    user_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    intent: Optional[str] = None,
    transaction_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    history_service = HistoryService(db)
    return history_service.get_user_histories(user_id, page, page_size, intent, transaction_type, start_date, end_date)


@user_routes.get("/me/receipts", response_model=List[ReceiptResponse])
def get_my_receipts(
    authjwt: AuthJWT = Depends(validate_token),
    limit: int = Query(10, ge=1),
    db: Session = Depends(get_db)
):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    receipt_service = ReceiptService(db)
    return receipt_service.get_user_receipts(user.id, limit)


@user_routes.get("/{user_id}/receipts", response_model=List[ReceiptResponse])
def get_user_receipts(
    user_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    limit: int = Query(10, ge=1),
    db: Session = Depends(get_db)
):
    receipt_service = ReceiptService(db)
    return receipt_service.get_user_receipts(user_id, limit)


@user_routes.put("/me", response_model=UserResponse)
def update_current_user_endpoint(payload: UserUpdateRequest, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    return user_service.update_current_user(current_user_email, payload)


@user_routes.patch("/me", response_model=UserResponse)
def patch_current_user_endpoint(payload: UserUpdateRequest, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    return user_service.update_current_user(current_user_email, payload)


@user_routes.patch("/me/notification-settings", response_model=UserResponse)
def update_my_notification_settings(
    payload: NotificationSettingsUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Update only notification preference flags for the current user.
    Allowed fields: in_app_notification, sms_notification
    """
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    data = payload.model_dump(exclude_unset=True)
    return user_service.update_current_user_notification_settings(
        current_user_email,
        in_app_notification=data.get("in_app_notification"),
        sms_notification=data.get("sms_notification"),
    )


@user_routes.get("/me/agents", response_model=UserAgentsResponse)
def get_my_agents(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    return UserAgentsResponse(
        agents=user.agents or {},
        available_agents=list(AGENT_REQUIRED_PARAMS.keys()),
    )


@user_routes.patch("/me/agents/{agent_name}", response_model=UserAgentsResponse)
def update_my_agent(
    agent_name: str,
    payload: AgentUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    if not user.phone:
        raise HTTPException(status_code=400, detail="Phone number required to configure agents")

    svc = AgentConfigService(db)
    result = svc.create_or_update_agent(
        user.phone,
        agent_name,
        payload.params,
        status=payload.status or "active",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to update agent"))

    user = user_service.get_current_user(current_user_email)
    return UserAgentsResponse(
        agents=user.agents or {},
        available_agents=list(AGENT_REQUIRED_PARAMS.keys()),
    )


@user_routes.patch("/me/profile-image", response_model=UserResponse)
def update_my_profile_image(
    payload: ProfileImageUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Update only profile picture URL for the current user.
    Allowed field: profile_picture_url
    """
    current_user_email = authjwt.get_jwt_subject()
    user_service = UserService(db)
    return user_service.update_current_user_profile_image(
        current_user_email, profile_picture_url=str(payload.profile_picture_url)
    )


@user_routes.patch("/{user_id}/notification-settings", response_model=UserResponse)
def update_user_notification_settings(
    user_id: str,
    payload: NotificationSettingsUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Update only notification preference flags for a specific user.
    Allowed fields: in_app_notification, sms_notification
    """
    user_service = UserService(db)
    data = payload.model_dump(exclude_unset=True)
    return user_service.update_user_notification_settings(
        user_id,
        in_app_notification=data.get("in_app_notification"),
        sms_notification=data.get("sms_notification"),
    )


@user_routes.patch("/{user_id}/profile-image", response_model=UserResponse)
def update_user_profile_image(
    user_id: str,
    payload: ProfileImageUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Update only profile picture URL for a specific user.
    Allowed field: profile_picture_url
    """
    user_service = UserService(db)
    return user_service.update_user_profile_image(
        user_id, profile_picture_url=str(payload.profile_picture_url)
    )
    
@user_routes.get("/all", response_model=PagedUserResponse)
def get_all_users(
    authjwt: AuthJWT = Depends(validate_token),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1),
    db: Session = Depends(get_db)
):
    user_service = UserService(db)
    return user_service.get_all_users_paged(page, size)

@user_routes.get("/{user_id}", response_model=UserResponse)
def get_user_by_id(user_id: str, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    # Add admin check here if needed
    user_service = UserService(db)
    return user_service.get_user_by_id(user_id)


@user_routes.put("/{user_id}", response_model=UserResponse)
def update_user_endpoint(user_id: str, payload: UserUpdateRequest, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    user_service = UserService(db)
    return user_service.update_user(user_id, payload)


@user_routes.patch("/{user_id}", response_model=UserResponse)
def patch_user_endpoint(user_id: str, payload: UserUpdateRequest, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    user_service = UserService(db)
    return user_service.update_user(user_id, payload)

@user_routes.put("/{user_id}/status", response_model=MessageResponse)
def update_user_status(user_id: str, enabled: bool = Query(...), authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    # Add admin check here if needed
    user_service = UserService(db)
    user_service.set_user_enabled_status(user_id, enabled)
    return {"message": "User status updated successfully"}

@user_routes.delete("/{user_id}", response_model=MessageResponse)
def delete_user(user_id: str, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    # Add admin check here if needed
    user_service = UserService(db)
    return user_service.delete_user(user_id)

@user_routes.put("/{user_id}/role/{role_id}", response_model=MessageResponse)
def update_user_role(user_id: str, role_id: str, authjwt: AuthJWT = Depends(validate_token), db: Session = Depends(get_db)):
    # Add admin check here if needed
    user_service = UserService(db)
    return user_service.update_user_role(user_id, role_id)