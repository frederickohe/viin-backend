"""
Authenticated probes for self-hosted Postiz and Chatwoot (reachability + optional provision).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.chatwoot.controller.chatwoot_controller import (
    resolve_internal_user_id,
    validate_jwt_subject,
)
from core.chatwoot.model.ChatwootAccount import ChatwootAccount
from core.chatwoot.service.chatwoot_api_service import (
    ChatwootAPIError,
    ChatwootClient,
    chatwoot_enabled,
    derive_chatwoot_password,
)
from core.chatwoot.service.chatwoot_org_service import ChatwootOrgService
from core.integrations.dto.integration_test_dto import (
    ExternalHealthResponse,
    IntegrationProbeResult,
    IntegrationSelfTestRequest,
    IntegrationSelfTestResponse,
    IntegrationServiceResult,
)
from core.socialmedia.model.PostizOrganization import PostizOrganization
from core.socialmedia.service.postiz_api_service import (
    PostizAPIError,
    PostizClient,
    derive_postiz_password,
    normalize_postiz_company,
    postiz_enabled,
)
from core.socialmedia.service.postiz_org_service import PostizOrgService
from core.user.model.User import User
from utilities.crypto import encrypt_secret
from utilities.dbconfig import get_db

logger = logging.getLogger(__name__)

integration_routes = APIRouter()


def _self_test_enabled() -> bool:
    return os.getenv("INTEGRATIONS_SELF_TEST_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def _probe_postiz() -> IntegrationProbeResult:
    base = os.getenv("POSTIZ_BASE_URL", "").strip()
    if not base:
        return IntegrationProbeResult(configured=False, ok=False, detail="POSTIZ_BASE_URL not set")
    url = f"{base.rstrip('/')}/api/auth/can-register"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url)
        body: Dict[str, Any] = {}
        if res.text.strip():
            try:
                body = res.json()
            except Exception:
                body = {"raw": res.text[:500]}
        ok = res.status_code < 400
        return IntegrationProbeResult(
            configured=True,
            ok=ok,
            http_status=res.status_code,
            detail=None if ok else (res.text[:500] if res.text else None),
            data=body if ok else None,
        )
    except Exception as e:
        logger.warning("[INTEGRATIONS] Postiz health probe failed: %s", e)
        return IntegrationProbeResult(
            configured=True,
            ok=False,
            detail=str(e),
        )


async def _probe_chatwoot() -> IntegrationProbeResult:
    if not chatwoot_enabled():
        base = os.getenv("CHATWOOT_BASE_URL", "").strip()
        tok = bool(os.getenv("CHATWOOT_PLATFORM_API_TOKEN", "").strip())
        return IntegrationProbeResult(
            configured=False,
            ok=False,
            detail=f"CHATWOOT_BASE_URL set={bool(base)}, CHATWOOT_PLATFORM_API_TOKEN set={tok}",
        )
    base_url = os.getenv("CHATWOOT_BASE_URL", "").strip()
    token = os.getenv("CHATWOOT_PLATFORM_API_TOKEN", "").strip()
    try:
        data = await ChatwootClient(base_url=base_url, platform_api_token=token).list_accounts(
            page=1
        )
        payload = data.get("payload")
        if not isinstance(payload, list):
            payload = data.get("data")
        acct_count = len(payload) if isinstance(payload, list) else None
        return IntegrationProbeResult(
            configured=True,
            ok=True,
            http_status=200,
            data={"account_count": acct_count},
        )
    except ChatwootAPIError as e:
        return IntegrationProbeResult(configured=True, ok=False, detail=str(e))
    except Exception as e:
        logger.warning("[INTEGRATIONS] Chatwoot health probe failed: %s", e)
        return IntegrationProbeResult(configured=True, ok=False, detail=str(e))


@integration_routes.get("/external-health", response_model=ExternalHealthResponse)
async def external_health(
    _subject: str = Depends(validate_jwt_subject),
) -> ExternalHealthResponse:
    """
    Read-only checks: Postiz `GET /api/auth/can-register` and Chatwoot platform `GET /accounts`.
    Requires a valid Autobus JWT (avoids exposing integration status anonymously).
    """
    postiz_task = asyncio.create_task(_probe_postiz())
    chatwoot_task = asyncio.create_task(_probe_chatwoot())
    postiz, chatwoot = await asyncio.gather(postiz_task, chatwoot_task)
    return ExternalHealthResponse(postiz=postiz, chatwoot=chatwoot)


@integration_routes.post("/provision-self-test", response_model=IntegrationSelfTestResponse)
async def provision_self_test(
    body: IntegrationSelfTestRequest,
    jwt_subject: str = Depends(validate_jwt_subject),
    db: Session = Depends(get_db),
) -> IntegrationSelfTestResponse:
    """
    Runs the same Postiz / Chatwoot provisioning logic used on subscribe for the **current user**.

    Set environment variable ``INTEGRATIONS_SELF_TEST_ENABLED=true`` on the server before calling,
    otherwise this endpoint returns 403.

    When ``persist_db`` is false, created API tokens are returned once in the JSON response
    (use only on staging). When true, rows are written like production subscribe.
    """
    if not _self_test_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Set INTEGRATIONS_SELF_TEST_ENABLED=true to use this endpoint.",
        )

    internal_id = resolve_internal_user_id(db, jwt_subject)
    user = db.query(User).filter(User.id == internal_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no email; cannot provision Postiz/Chatwoot.",
        )

    postiz_result = IntegrationServiceResult(attempted=False)
    chatwoot_result = IntegrationServiceResult(attempted=False)

    company_name = normalize_postiz_company(
        (user.company or user.organization_workplace or user.fullname or "Autobus Client").strip()
    )

    if body.postiz:
        postiz_result = await _run_postiz_provision(
            db=db,
            user=user,
            company_name=company_name,
            persist_db=body.persist_db,
        )

    if body.chatwoot:
        chatwoot_result = await _run_chatwoot_provision(
            db=db,
            user=user,
            account_name=(user.company or user.organization_workplace or user.fullname or "Autobus Client").strip()
            or "Autobus Client",
            persist_db=body.persist_db,
        )

    return IntegrationSelfTestResponse(
        user_id=internal_id,
        persist_db=body.persist_db,
        postiz=postiz_result,
        chatwoot=chatwoot_result,
    )


async def _run_postiz_provision(
    *,
    db: Session,
    user: User,
    company_name: str,
    persist_db: bool,
) -> IntegrationServiceResult:
    if not postiz_enabled():
        return IntegrationServiceResult(
            attempted=False,
            ok=False,
            skipped_reason="POSTIZ_BASE_URL not configured",
        )

    existing = PostizOrgService(db).get_for_user(user.id)
    if existing:
        return IntegrationServiceResult(
            attempted=False,
            ok=True,
            skipped_reason="postiz_organizations mapping already exists for this user",
        )

    base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
    postiz_password = derive_postiz_password(
        user_id=user.id,
        email=user.email,
        autobus_password_hash=user.hashed_password,
    )
    client = PostizClient(base_url=base_url)
    try:
        postiz_org_id, postiz_api_key = await client.provision_org_and_get_public_api_key(
            email=user.email,
            company=company_name,
            password=postiz_password,
        )
    except PostizAPIError as e:
        return IntegrationServiceResult(attempted=True, ok=False, error=str(e))

    if persist_db:
        mapping = PostizOrganization(
            id=f"po_{str(uuid.uuid4())[:12]}",
            user_id=user.id,
            postiz_org_id=postiz_org_id,
            postiz_public_api_key_encrypted=encrypt_secret(postiz_api_key) or postiz_api_key,
        )
        db.add(mapping)
        db.commit()
        return IntegrationServiceResult(
            attempted=True,
            ok=True,
            postiz_org_id=str(postiz_org_id),
        )

    return IntegrationServiceResult(
        attempted=True,
        ok=True,
        postiz_org_id=str(postiz_org_id),
        postiz_public_api_key=postiz_api_key,
    )


async def _run_chatwoot_provision(
    *,
    db: Session,
    user: User,
    account_name: str,
    persist_db: bool,
) -> IntegrationServiceResult:
    if not chatwoot_enabled():
        return IntegrationServiceResult(
            attempted=False,
            ok=False,
            skipped_reason="CHATWOOT_BASE_URL or CHATWOOT_PLATFORM_API_TOKEN not configured",
        )

    existing = ChatwootOrgService(db).get_for_user(user.id)
    if existing:
        return IntegrationServiceResult(
            attempted=False,
            ok=True,
            skipped_reason="chatwoot_accounts mapping already exists for this user",
        )

    base_url = os.getenv("CHATWOOT_BASE_URL", "").strip()
    token = os.getenv("CHATWOOT_PLATFORM_API_TOKEN", "").strip()
    chatwoot_password = derive_chatwoot_password(
        user_id=user.id,
        email=user.email,
        autobus_password_hash=user.hashed_password,
    )
    cw_client = ChatwootClient(base_url=base_url, platform_api_token=token)
    try:
        cw_account_id, cw_user_id, cw_access_token = await cw_client.provision_account_and_user(
            account_name=account_name or "Autobus Client",
            email=user.email,
            name=(user.fullname or user.email).strip(),
            password=chatwoot_password,
            support_email=user.email,
        )
    except ChatwootAPIError as e:
        return IntegrationServiceResult(attempted=True, ok=False, error=str(e))

    if persist_db:
        mapping = ChatwootAccount(
            id=f"cw_{str(uuid.uuid4())[:12]}",
            user_id=user.id,
            chatwoot_account_id=int(cw_account_id),
            chatwoot_user_id=int(cw_user_id),
            chatwoot_user_access_token_encrypted=encrypt_secret(cw_access_token) or cw_access_token,
        )
        db.add(mapping)
        db.commit()
        return IntegrationServiceResult(
            attempted=True,
            ok=True,
            chatwoot_account_id=int(cw_account_id),
            chatwoot_user_id=int(cw_user_id),
        )

    return IntegrationServiceResult(
        attempted=True,
        ok=True,
        chatwoot_account_id=int(cw_account_id),
        chatwoot_user_id=int(cw_user_id),
        chatwoot_user_access_token=cw_access_token,
    )
