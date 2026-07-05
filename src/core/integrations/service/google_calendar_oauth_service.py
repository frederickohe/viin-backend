from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy.orm import Session

from config import settings
from core.integrations.model.google_calendar_connection import GoogleCalendarConnection
from core.user.model.User import User
from utilities.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
STATE_PURPOSE = "google_calendar_connect"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GoogleCalendarOAuthService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def is_configured() -> bool:
        return bool(
            settings.GOOGLE_OAUTH_CLIENT_ID.strip()
            and settings.GOOGLE_OAUTH_CLIENT_SECRET.strip()
            and settings.GOOGLE_OAUTH_REDIRECT_URI.strip()
        )

    def build_authorization_url(self, *, user: User) -> str:
        if not self.is_configured():
            raise ValueError("Google OAuth is not configured")

        state = jwt.encode(
            {
                "sub": user.id,
                "purpose": STATE_PURPOSE,
                "exp": _now() + timedelta(minutes=15),
            },
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

        params = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": GOOGLE_CALENDAR_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    def handle_callback(self, *, code: str, state: str) -> GoogleCalendarConnection:
        user_id = self._decode_state(state)
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found for OAuth state")

        token_data = self._exchange_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Google did not return an access token")

        refresh_token = token_data.get("refresh_token")
        expires_in = int(token_data.get("expires_in", 3600))
        token_expires_at = _now() + timedelta(seconds=expires_in)
        google_email = self._fetch_google_email(access_token)

        existing = (
            self.db.query(GoogleCalendarConnection)
            .filter(GoogleCalendarConnection.user_id == user.id)
            .first()
        )

        if existing:
            conn = existing
            conn.access_token_enc = encrypt_secret(access_token) or access_token
            if refresh_token:
                conn.refresh_token_enc = encrypt_secret(refresh_token) or refresh_token
            conn.token_expires_at = token_expires_at
            conn.google_account_email = google_email or conn.google_account_email
            conn.enabled = True
            conn.last_sync_error = None
            conn.updated_at = _now()
        else:
            conn = GoogleCalendarConnection(
                id=uuid.uuid4().hex,
                user_id=user.id,
                google_account_email=google_email,
                calendar_id="primary",
                access_token_enc=encrypt_secret(access_token) or access_token,
                refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else None,
                token_expires_at=token_expires_at,
                reminder_lead_minutes=settings.GOOGLE_CALENDAR_REMINDER_LEAD_MINUTES,
                enabled=True,
                created_at=_now(),
                updated_at=_now(),
            )
            self.db.add(conn)

        self.db.commit()
        self.db.refresh(conn)
        return conn

    def get_connection(self, user_id: str) -> Optional[GoogleCalendarConnection]:
        return (
            self.db.query(GoogleCalendarConnection)
            .filter(GoogleCalendarConnection.user_id == user_id)
            .first()
        )

    def disconnect(self, user_id: str) -> bool:
        conn = self.get_connection(user_id)
        if not conn:
            return False

        access_token = decrypt_secret(conn.access_token_enc)
        if access_token:
            try:
                httpx.post(
                    GOOGLE_REVOKE_URL,
                    params={"token": access_token},
                    timeout=10.0,
                )
            except Exception as exc:
                logger.warning("[GOOGLE_CALENDAR] token revoke failed user=%s err=%s", user_id, exc)

        self.db.delete(conn)
        self.db.commit()
        return True

    def get_valid_access_token(self, conn: GoogleCalendarConnection) -> str:
        access_token = decrypt_secret(conn.access_token_enc)
        if not access_token:
            raise ValueError("Missing Google access token")

        expires_at = conn.token_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at and expires_at > _now() + timedelta(minutes=2):
            return access_token

        refresh_token = decrypt_secret(conn.refresh_token_enc)
        if not refresh_token:
            raise ValueError("Google access token expired and no refresh token is available")

        token_data = self._refresh_access_token(refresh_token)
        new_access = token_data.get("access_token")
        if not new_access:
            raise ValueError("Google token refresh did not return an access token")

        conn.access_token_enc = encrypt_secret(new_access) or new_access
        if token_data.get("refresh_token"):
            conn.refresh_token_enc = encrypt_secret(token_data["refresh_token"]) or token_data["refresh_token"]
        conn.token_expires_at = _now() + timedelta(seconds=int(token_data.get("expires_in", 3600)))
        conn.updated_at = _now()
        self.db.add(conn)
        self.db.commit()
        return new_access

    @staticmethod
    def _decode_state(state: str) -> str:
        try:
            payload = jwt.decode(state, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except jwt.PyJWTError as exc:
            raise ValueError("Invalid OAuth state") from exc

        if payload.get("purpose") != STATE_PURPOSE:
            raise ValueError("Invalid OAuth state purpose")
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("OAuth state missing user id")
        return str(user_id)

    def _exchange_code(self, code: str) -> dict:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()

    def _refresh_access_token(self, refresh_token: str) -> dict:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _fetch_google_email(access_token: str) -> Optional[str]:
        try:
            response = httpx.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json().get("email")
        except Exception as exc:
            logger.warning("[GOOGLE_CALENDAR] failed to fetch Google profile email: %s", exc)
            return None
