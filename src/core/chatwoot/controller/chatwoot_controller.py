"""
Chatwoot routes: subscription-gated workspace + channel linking (mirrors Postiz patterns
in `socialmedia_controller`).
"""

import asyncio
import logging
import os
from typing import Optional, Tuple

from another_fastapi_jwt_auth import AuthJWT
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.chatwoot.dto.chatwoot_channel_dto import (
    ChatwootChannelLinkResponse,
    ChatwootInboxesListResponse,
    ChatwootSessionResponse,
    ChatwootStatusResponse,
)
from core.chatwoot.model.ChatwootAccount import ChatwootAccount
from core.chatwoot.service.chatwoot_api_service import (
    ChatwootAccountClient,
    ChatwootAPIError,
    chatwoot_enabled,
    derive_chatwoot_password,
)
from core.chatwoot.service.chatwoot_org_service import ChatwootOrgService
from core.chatwoot.service.chatwoot_provision_service import ensure_chatwoot_provisioned
from core.subscription.service.subscription_service import SubscriptionService
from core.user.model.User import User
from utilities.dbconfig import get_db

logger = logging.getLogger(__name__)

chatwoot_routes = APIRouter()

_CHANNEL_ALIASES = {
    "wa": "whatsapp",
    "fb": "facebook",
    "ig": "instagram",
    "tw": "twitter",
    "x": "twitter",
    "li": "linkedin",
    "tg": "telegram",
}

_VALID_CHANNELS = frozenset(
    {
        "whatsapp",
        "facebook",
        "instagram",
        "twitter",
        "linkedin",
        "telegram",
        "line",
        "sms",
        "website",
        "email",
        "api",
    }
)


def validate_jwt_subject(authjwt: AuthJWT = Depends()) -> str:
    try:
        authjwt.jwt_required()
        return authjwt.get_jwt_subject()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def resolve_internal_user_id(db: Session, jwt_subject: str) -> str:
    """
    JWT `sub` is the user's email at login (`AuthService.signin`).
    Some routes may use internal id as `sub`; support both.
    """
    user = db.query(User).filter(User.email == jwt_subject).first()
    if user:
        return user.id
    user = db.query(User).filter(User.id == jwt_subject).first()
    if user:
        return user.id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authenticated subject does not match a user",
    )


def _resolve_public_url() -> str:
    return (os.getenv("CHATWOOT_PUBLIC_URL", "").strip() or os.getenv("CHATWOOT_BASE_URL", "").strip())


def _resolve_api_base() -> str:
    return os.getenv("CHATWOOT_BASE_URL", "").strip()


def _inboxes_settings_url(public_url: str, account_id: int) -> str:
    return f"{public_url.rstrip('/')}/app/accounts/{int(account_id)}/settings/inboxes"


def _normalize_channel(raw: str) -> str:
    c = (raw or "").strip().lower()
    if not c:
        raise HTTPException(status_code=400, detail="Missing channel")
    c = _CHANNEL_ALIASES.get(c, c)
    if c not in _VALID_CHANNELS:
        supported = ", ".join(sorted(_VALID_CHANNELS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported channel '{raw}'. Supported: {supported}",
        )
    return c


def _require_subscription(db: Session, internal_user_id: str) -> None:
    sub = SubscriptionService(db).get_user_active_subscription(internal_user_id)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An active subscription is required to use Chatwoot features.",
        )


def _require_chatwoot_env() -> None:
    if not chatwoot_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chatwoot is not configured (CHATWOOT_BASE_URL and CHATWOOT_PLATFORM_API_TOKEN).",
        )


def _mapping_and_client(db: Session, internal_user_id: str) -> Tuple[ChatwootAccount, ChatwootAccountClient]:
    org = ChatwootOrgService(db)
    mapping = org.get_for_user(internal_user_id)
    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Chatwoot workspace linked yet. Subscribe to provision Chatwoot, then retry.",
        )
    token = org.get_user_access_token(internal_user_id)
    if not token:
        raise HTTPException(status_code=500, detail="Chatwoot access token could not be decrypted.")
    base = _resolve_api_base()
    if not base:
        raise HTTPException(status_code=400, detail="CHATWOOT_BASE_URL not configured.")
    client = ChatwootAccountClient(
        base_url=base,
        account_id=int(mapping.chatwoot_account_id),
        user_access_token=token,
    )
    return mapping, client


async def _probe_token(client: ChatwootAccountClient) -> bool:
    def _run() -> None:
        try:
            client.get_profile(timeout_s=10.0)
        except ChatwootAPIError:
            client.list_inboxes(timeout_s=10.0)

    try:
        await asyncio.to_thread(_run)
        return True
    except Exception as e:
        logger.warning("[CHATWOOT] Token probe failed: %s", e)
        return False


@chatwoot_routes.get("/status", response_model=ChatwootStatusResponse)
async def chatwoot_status(
    jwt_subject: str = Depends(validate_jwt_subject),
    db: Session = Depends(get_db),
):
    internal_id = resolve_internal_user_id(db, jwt_subject)
    sub_active = bool(SubscriptionService(db).get_user_active_subscription(internal_id))
    configured = chatwoot_enabled()
    if sub_active and configured:
        await ensure_chatwoot_provisioned(db, internal_id)
    mapping = ChatwootOrgService(db).get_for_user(internal_id)
    provisioned = mapping is not None
    token_valid: Optional[bool] = None
    acct_id: Optional[int] = None
    if mapping:
        acct_id = int(mapping.chatwoot_account_id)
    if provisioned and configured:
        try:
            _, client = _mapping_and_client(db, internal_id)
            token_valid = await _probe_token(client)
        except HTTPException:
            token_valid = False
    return ChatwootStatusResponse(
        chatwoot_configured=configured,
        subscription_active=sub_active,
        chatwoot_provisioned=provisioned,
        chatwoot_account_id=acct_id,
        token_valid=token_valid,
    )


@chatwoot_routes.get("/session", response_model=ChatwootSessionResponse)
async def chatwoot_session(
    jwt_subject: str = Depends(validate_jwt_subject),
    db: Session = Depends(get_db),
):
    _require_chatwoot_env()
    internal_id = resolve_internal_user_id(db, jwt_subject)
    _require_subscription(db, internal_id)
    await ensure_chatwoot_provisioned(db, internal_id)
    user = db.query(User).filter(User.id == internal_id).first()
    if not user or not user.email:
        raise HTTPException(status_code=404, detail="User email not found.")

    mapping, client = _mapping_and_client(db, internal_id)
    public_url = _resolve_public_url()
    if not public_url:
        raise HTTPException(status_code=400, detail="CHATWOOT_PUBLIC_URL or CHATWOOT_BASE_URL must be set.")

    auth_url = _inboxes_settings_url(public_url, mapping.chatwoot_account_id)
    pwd = derive_chatwoot_password(username=user.fullname)
    ready = await _probe_token(client)

    return ChatwootSessionResponse(
        chatwoot_account_id=int(mapping.chatwoot_account_id),
        chatwoot_public_url=public_url.rstrip("/"),
        authorization_url=auth_url,
        chatwoot_login_ready=ready,
        chatwoot_login={
            "login_page_url": f"{public_url.rstrip('/')}/app/login",
            "body": {"email": user.email, "password": pwd},
        },
        message=(
            "Open login_page_url in a WebView or browser, sign in with chatwoot_login.body, "
            "then navigate to authorization_url to add messaging inboxes (WhatsApp, Twitter, etc.)."
        ),
    )


@chatwoot_routes.get("/channels/{channel}/link", response_model=ChatwootChannelLinkResponse)
async def chatwoot_channel_link(
    channel: str,
    jwt_subject: str = Depends(validate_jwt_subject),
    db: Session = Depends(get_db),
):
    _require_chatwoot_env()
    internal_id = resolve_internal_user_id(db, jwt_subject)
    _require_subscription(db, internal_id)
    await ensure_chatwoot_provisioned(db, internal_id)
    ch = _normalize_channel(channel)
    user = db.query(User).filter(User.id == internal_id).first()
    if not user or not user.email:
        raise HTTPException(status_code=404, detail="User email not found.")

    mapping, client = _mapping_and_client(db, internal_id)
    public_url = _resolve_public_url()
    if not public_url:
        raise HTTPException(status_code=400, detail="CHATWOOT_PUBLIC_URL or CHATWOOT_BASE_URL must be set.")

    auth_url = _inboxes_settings_url(public_url, mapping.chatwoot_account_id)
    pwd = derive_chatwoot_password(username=user.fullname)
    ready = await _probe_token(client)

    autobus_meta: Optional[str] = None
    if ch == "whatsapp":
        base = os.getenv("AUTOBUS_PUBLIC_API_URL", "").strip()
        if base:
            autobus_meta = f"{base.rstrip('/')}/api/v1/webhooks/start-dialog"

    return ChatwootChannelLinkResponse(
        channel=ch,
        chatwoot_account_id=int(mapping.chatwoot_account_id),
        chatwoot_public_url=public_url.rstrip("/"),
        authorization_url=auth_url,
        channel_hint=ch,
        chatwoot_login_ready=ready,
        chatwoot_login={
            "login_page_url": f"{public_url.rstrip('/')}/app/login",
            "body": {"email": user.email, "password": pwd},
        },
        autobus_meta_webhook_url=autobus_meta,
        message=(
            "In Chatwoot, add this channel at authorization_url (use channel_hint to pick the provider). "
            "If you route WhatsApp through Autobus instead of Chatwoot, point Meta to autobus_meta_webhook_url when configured."
        ),
    )


@chatwoot_routes.get("/inboxes", response_model=ChatwootInboxesListResponse)
async def chatwoot_list_inboxes(
    jwt_subject: str = Depends(validate_jwt_subject),
    db: Session = Depends(get_db),
):
    _require_chatwoot_env()
    internal_id = resolve_internal_user_id(db, jwt_subject)
    _require_subscription(db, internal_id)
    await ensure_chatwoot_provisioned(db, internal_id)

    def _load() -> list:
        _, client = _mapping_and_client(db, internal_id)
        return client.list_inboxes(timeout_s=20.0)

    try:
        rows = await asyncio.to_thread(_load)
    except ChatwootAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return ChatwootInboxesListResponse(inboxes=rows, total=len(rows))
