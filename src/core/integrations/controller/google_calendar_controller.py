from __future__ import annotations

import jwt
from another_fastapi_jwt_auth import AuthJWT
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import settings
from core.auth.controller.authcontroller import get_db
from core.integrations.dto.google_calendar_dtos import (
    GoogleCalendarConnectResponse,
    GoogleCalendarDisconnectResponse,
    GoogleCalendarSettingsUpdateRequest,
    GoogleCalendarStatusResponse,
    GoogleCalendarSyncResponse,
)
from core.integrations.service.google_calendar_oauth_service import GoogleCalendarOAuthService
from core.integrations.service.google_calendar_sync_service import GoogleCalendarSyncService
from core.user.service.user_service import UserService

google_calendar_routes = APIRouter()


def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired. Please log in again.")
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(exc)}")


def _status_from_connection(conn) -> GoogleCalendarStatusResponse:
    return GoogleCalendarStatusResponse(
        connected=bool(conn and conn.enabled),
        google_account_email=getattr(conn, "google_account_email", None),
        calendar_id=getattr(conn, "calendar_id", None),
        reminder_lead_minutes=getattr(conn, "reminder_lead_minutes", 15),
        last_synced_at=getattr(conn, "last_synced_at", None),
        last_sync_error=getattr(conn, "last_sync_error", None),
        enabled=bool(getattr(conn, "enabled", False)),
    )


@google_calendar_routes.get("/connect", response_model=GoogleCalendarConnectResponse)
def connect_google_calendar(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    if not GoogleCalendarOAuthService.is_configured():
        raise HTTPException(status_code=503, detail="Google Calendar integration is not configured")

    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    oauth = GoogleCalendarOAuthService(db)

    try:
        authorization_url = oauth.build_authorization_url(user=user)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return GoogleCalendarConnectResponse(authorization_url=authorization_url)


@google_calendar_routes.get("/callback")
def google_calendar_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    frontend_base = settings.BASE_FRONTEND_URL.rstrip("/")
    success_url = f"{frontend_base}/dashboard/integrations?connected=google_calendar"
    error_url = f"{frontend_base}/dashboard/integrations?error=google_calendar"

    oauth = GoogleCalendarOAuthService(db)
    sync = GoogleCalendarSyncService(db)

    try:
        conn = oauth.handle_callback(code=code, state=state)
        try:
            sync.sync_connection(conn)
        except Exception:
            # Connection succeeded; background sync will retry.
            pass
        return RedirectResponse(url=success_url, status_code=302)
    except Exception:
        return RedirectResponse(url=error_url, status_code=302)


@google_calendar_routes.get("/status", response_model=GoogleCalendarStatusResponse)
def google_calendar_status(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    conn = GoogleCalendarOAuthService(db).get_connection(user.id)
    return _status_from_connection(conn)


@google_calendar_routes.patch("/settings", response_model=GoogleCalendarStatusResponse)
def update_google_calendar_settings(
    payload: GoogleCalendarSettingsUpdateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    oauth = GoogleCalendarOAuthService(db)
    conn = oauth.get_connection(user.id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected")

    if payload.reminder_lead_minutes is not None:
        conn.reminder_lead_minutes = payload.reminder_lead_minutes
        db.add(conn)
        db.commit()
        db.refresh(conn)

    return _status_from_connection(conn)


@google_calendar_routes.post("/sync", response_model=GoogleCalendarSyncResponse)
def sync_google_calendar_now(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    oauth = GoogleCalendarOAuthService(db)
    conn = oauth.get_connection(user.id)
    if not conn or not conn.enabled:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected")

    stats = GoogleCalendarSyncService(db).sync_connection(conn)
    return GoogleCalendarSyncResponse(
        synced_events=stats.synced_events,
        reminders_created=stats.reminders_created,
        reminders_updated=stats.reminders_updated,
        reminders_cancelled=stats.reminders_cancelled,
    )


@google_calendar_routes.delete("/disconnect", response_model=GoogleCalendarDisconnectResponse)
def disconnect_google_calendar(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    disconnected = GoogleCalendarOAuthService(db).disconnect(user.id)
    if not disconnected:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected")
    return GoogleCalendarDisconnectResponse(message="Google Calendar disconnected")
