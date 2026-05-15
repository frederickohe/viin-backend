"""
Social Media Controller
API routes for social media account management and posting
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import os
from typing import Any, Dict
import uuid

from fastapi.responses import JSONResponse

from another_fastapi_jwt_auth import AuthJWT
from core.socialmedia.dto.socialmedia_dto import (
    SocialAccountResponse, SocialAccountsListResponse, DisconnectAccountRequest,
    PublishPostRequest, PublishPostResponse, RefreshAccountsRequest,
    RefreshAccountsResponse, OAuth2CallbackRequest, ErrorResponse,
    SocialPlatformEnum,
    DigitalMarketingAssetListResponse,
    DigitalMarketingAssetResponse,
    DigitalMarketingAssetDetailResponse,
)
from core.socialmedia.service.socialmedia_service import SocialMediaService
from core.socialmedia.service.post_publishing_service import PostPublishingService
from core.socialmedia.service.blotato_api_service import (
    BlotatoAPIClient, BlotatoOAuthManager
)
from core.socialmedia.service.postiz_api_service import PostizClient, PostizAPIError, derive_postiz_password
from core.socialmedia.service.postiz_marketing_extract import (
    extract_marketing_text_and_links,
    normalize_digital_marketing_agent_name,
)
from core.socialmedia.service.digital_marketing_asset_service import DigitalMarketingAssetService
from core.socialmedia.service.postiz_org_service import PostizOrgService
from core.socialmedia.model.PostizOrganization import PostizOrganization
from core.user.model.User import User
from utilities.crypto import encrypt_secret
from utilities.dbconfig import get_db

logger = logging.getLogger(__name__)

# Initialize router
social_routes = APIRouter()

# Initialize Blotato API Client (with environment variables)
BLOTATO_API_KEY = os.getenv("BLOTATO_API_KEY", "")
BLOTATO_CLIENT_ID = os.getenv("BLOTATO_CLIENT_ID", "")
BLOTATO_CLIENT_SECRET = os.getenv("BLOTATO_CLIENT_SECRET", "")

if not all([BLOTATO_API_KEY, BLOTATO_CLIENT_ID, BLOTATO_CLIENT_SECRET]):
    logger.warning("[SOCIAL] Blotato credentials not fully configured in environment variables")

blotato_client = BlotatoAPIClient(
    api_key=BLOTATO_API_KEY,
    client_id=BLOTATO_CLIENT_ID,
    client_secret=BLOTATO_CLIENT_SECRET
)

def _resolve_postiz_api_key(user_id: str, db: Session) -> Optional[str]:
    """
    Resolve Postiz Public API key for proxy routes.
    Priority:
      1) user-specific key stored in postiz_organizations
      2) global fallback key from env (for manual Postiz setup)
    """
    user_scoped_key = PostizOrgService(db).get_public_api_key_for_user(user_id)
    if user_scoped_key:
        return user_scoped_key

    return (
        os.getenv("POSTIZ_PUBLIC_API_KEY", "").strip()
        or os.getenv("POSTIZ_GLOBAL_PUBLIC_API_KEY", "").strip()
        or None
    )


async def _ensure_postiz_api_key(user_id: str, db: Session) -> Optional[str]:
    """
    Ensure the user has a Postiz org mapping + public API key.
    Returns API key when available.
    """
    existing_key = _resolve_postiz_api_key(user_id, db)
    if existing_key:
        return existing_key

    postiz_base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
    if not postiz_base_url:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    company_name = (user.company or user.fullname or "Autobus Client").strip()
    postiz_password = derive_postiz_password(
        user_id=user.id,
        email=user.email,
        autobus_password_hash=user.hashed_password,
    )
    client = PostizClient(base_url=postiz_base_url)
    postiz_org_id, postiz_api_key = await client.provision_org_and_get_public_api_key(
        email=user.email,
        company=company_name,
        password=postiz_password,
    )

    mapping = PostizOrganization(
        id=f"po_{str(uuid.uuid4())[:12]}",
        user_id=user.id,
        postiz_org_id=postiz_org_id,
        postiz_public_api_key_encrypted=encrypt_secret(postiz_api_key) or postiz_api_key,
    )
    db.add(mapping)
    db.commit()
    return postiz_api_key


# Dependency for token validation
def validate_token(authjwt: AuthJWT = Depends()) -> str:
    """Validate JWT token and return user ID"""
    try:
        authjwt.jwt_required()
        return authjwt.get_jwt_subject()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )


# ==================== OAuth Flow Routes ====================

@social_routes.get("/connect/{platform}")
async def initiate_oauth_flow(
    platform: str,
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Initiate OAuth flow for connecting a social media account
    
    Args:
        platform: Social media platform (twitter, linkedin, facebook, instagram, tiktok, etc.)
        user_id: Authenticated user ID
        
    Returns:
        Redirect URL to Blotato OAuth endpoint
    """
    try:
        # Normalize platform
        platform_upper = platform.upper()
        
        # Validate platform
        valid_platforms = [p.value for p in SocialPlatformEnum]
        if platform_upper not in valid_platforms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported platform. Supported: {', '.join(valid_platforms)}"
            )
        
        # Prefer Postiz flow for Facebook when Postiz is configured.
        postiz_base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
        if platform_upper == "FACEBOOK" and postiz_base_url:
            try:
                api_key = await _ensure_postiz_api_key(user_id, db)
            except Exception as postiz_error:
                logger.warning(f"[SOCIAL] Postiz provisioning failed for user {user_id}: {postiz_error}")
                api_key = _resolve_postiz_api_key(user_id, db)

            browser_postiz_url = (os.getenv("POSTIZ_PUBLIC_URL", "").strip() or postiz_base_url).rstrip("/")
            user = db.query(User).filter(User.id == user_id).first()
            postiz_login_ready = False
            postiz_login_payload: Dict[str, Any] = {}

            if user and user.email:
                postiz_password = derive_postiz_password(
                    user_id=user.id,
                    email=user.email,
                    autobus_password_hash=user.hashed_password,
                )
                # Warm Postiz session and validate creds using the same payload contract
                # expected by Postiz LOCAL auth.
                try:
                    await PostizClient(base_url=postiz_base_url).login_local(
                        email=user.email,
                        password=postiz_password,
                    )
                    postiz_login_ready = True
                except Exception as login_error:
                    logger.warning(f"[SOCIAL] Postiz auto-login failed for user {user_id}: {login_error}")

                # Frontend can call this payload directly from browser for a real user session.
                postiz_login_payload = {
                    "url": f"{browser_postiz_url}/api/auth/login",
                    "body": {
                        "email": user.email,
                        "password": postiz_password,
                        "providerToken": "",
                        "provider": "LOCAL",
                    },
                }

            return {
                "authorization_url": f"{browser_postiz_url}/integrations",
                "platform": platform_upper,
                "provider": "POSTIZ",
                "postiz_ready": bool(api_key),
                "postiz_login_ready": postiz_login_ready,
                "postiz_login": postiz_login_payload,
                "message": "Open this URL, sign in to Postiz, and connect your Facebook channel."
            }

        # Legacy Blotato OAuth flow
        # Create OAuth state for CSRF protection
        state = BlotatoOAuthManager.create_state(user_id, platform_upper)
        
        # Generate OAuth URL
        callback_url = f"{os.getenv('BASE_FRONTEND_URL', 'http://localhost:3000')}/api/social/callback"
        auth_url, _ = await blotato_client.generate_oauth_url(
            redirect_uri=callback_url,
            state=state
        )
        
        logger.info(f"[SOCIAL] OAuth flow initiated for user {user_id}, platform {platform_upper}")
        
        return {
            "authorization_url": auth_url,
            "platform": platform_upper,
            "message": "Redirect user to this URL to authorize account connection"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SOCIAL] Error initiating OAuth: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error initiating OAuth: {str(e)}"
        )


@social_routes.get("/callback")
async def oauth_callback(
    code: str,
    state: str,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Handle OAuth callback from Blotato
    
    Args:
        code: Authorization code from Blotato
        state: State parameter for CSRF validation
        error: Error code if user denied authorization
        error_description: Error description
        
    Returns:
        Account connection status
    """
    try:
        # Check for errors
        if error:
            logger.warning(f"[SOCIAL] OAuth error: {error} - {error_description}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Authorization failed: {error_description or error}"
            )
        
        # Validate state
        state_data = BlotatoOAuthManager.validate_state(state)
        if not state_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired state parameter"
            )
        
        user_id = state_data["user_id"]
        platform = state_data["platform"]
        
        # Exchange code for account info
        callback_url = f"{os.getenv('BASE_FRONTEND_URL', 'http://localhost:3000')}/api/social/callback"
        token_data = await blotato_client.exchange_auth_code(code, callback_url)
        
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to exchange authorization code"
            )
        
        # Get accounts from Blotato
        access_token = token_data.get("access_token")
        accounts = await blotato_client.get_accounts(access_token)
        
        if not accounts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve accounts from Blotato"
            )
        
        # Find account for this platform
        platform_account = next(
            (acc for acc in accounts if acc.get("platform", "").upper() == platform),
            None
        )
        
        if not platform_account:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No {platform} account found in Blotato"
            )
        
        # Store account in database
        social_service = SocialMediaService(db, blotato_client)
        success, account_obj, message = await social_service.connect_account(
            user_id=user_id,
            platform=platform,
            blotato_account_info=platform_account
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        logger.info(f"[SOCIAL] OAuth callback successful: {user_id} - {platform}")
        
        return {
            "success": True,
            "message": f"Account connected successfully",
            "platform": platform,
            "account_name": account_obj.account_name if account_obj else None,
            "redirect_url": f"{os.getenv('BASE_FRONTEND_URL', 'http://localhost:3000')}/social/accounts?connected=true"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SOCIAL] Error in OAuth callback: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Callback processing error: {str(e)}"
        )


# ==================== Account Management Routes ====================

@social_routes.get("/accounts", response_model=SocialAccountsListResponse)
async def get_user_accounts(
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Get all connected social media accounts for the user
    
    Returns:
        List of connected social accounts
    """
    try:
        social_service = SocialMediaService(db, blotato_client)
        accounts = social_service.get_user_accounts(user_id)
        
        logger.info(f"[SOCIAL] Retrieved {len(accounts)} accounts for user {user_id}")
        
        account_responses = [
            SocialAccountResponse.from_orm(acc) for acc in accounts
        ]
        
        return SocialAccountsListResponse(
            accounts=account_responses,
            total=len(account_responses)
        )
        
    except Exception as e:
        logger.error(f"[SOCIAL] Error getting accounts: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving accounts: {str(e)}"
        )


@social_routes.delete("/accounts/{account_id}")
async def disconnect_account(
    account_id: str,
    request: DisconnectAccountRequest,
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Disconnect a social media account
    
    Args:
        account_id: ID of account to disconnect
        request: Request body with disconnect options
        user_id: Authenticated user ID
        
    Returns:
        Disconnection status
    """
    try:
        social_service = SocialMediaService(db, blotato_client)
        success, message = await social_service.disconnect_account(account_id, user_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message
            )
        
        logger.info(f"[SOCIAL] Account disconnected: {account_id} by user {user_id}")
        
        return {
            "success": True,
            "message": message,
            "account_id": account_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SOCIAL] Error disconnecting account: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error disconnecting account: {str(e)}"
        )


@social_routes.post("/refresh", response_model=RefreshAccountsResponse)
async def refresh_accounts(
    request: RefreshAccountsRequest,
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Refresh user's connected accounts from Blotato
    
    Args:
        request: Refresh request with optional platform filter
        user_id: Authenticated user ID
        
    Returns:
        Updated list of connected accounts
    """
    try:
        # Get user's existing accounts to retrieve access token
        social_service = SocialMediaService(db, blotato_client)
        user_accounts = social_service.get_user_accounts(user_id)
        
        # Get access token from first account (or find from Blotato)
        # In production, store access token securely for the user
        if not user_accounts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No connected accounts found. Please connect an account first."
            )
        
        # Use first account's token as fallback
        access_token = user_accounts[0].access_token
        
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No valid access token found. Please reconnect your accounts."
            )
        
        # Refresh accounts
        success, accounts, message = await social_service.refresh_accounts(
            user_id=user_id,
            access_token=access_token,
            platforms=request.platforms
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        logger.info(f"[SOCIAL] Refreshed accounts for user {user_id}: {message}")
        
        account_responses = [
            SocialAccountResponse.from_orm(acc) for acc in accounts
        ]
        
        return RefreshAccountsResponse(
            success=True,
            refreshed_count=len(account_responses),
            accounts=account_responses,
            message=message
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SOCIAL] Error refreshing accounts: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error refreshing accounts: {str(e)}"
        )


# ==================== Post Publishing Routes ====================

@social_routes.post("/post", response_model=PublishPostResponse)
async def publish_post(
    request: PublishPostRequest,
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Publish content to one or more connected social media accounts
    
    Args:
        request: Post content and target accounts
        user_id: Authenticated user ID
        
    Returns:
        Publishing results for each platform
    """
    try:
        # Get user's access token for media uploads
        social_service = SocialMediaService(db, blotato_client)
        user_accounts = social_service.get_user_accounts(user_id)
        
        access_token = None
        if user_accounts:
            access_token = user_accounts[0].access_token
        
        # Publish post
        publishing_service = PostPublishingService(db, blotato_client)
        response = await publishing_service.publish_post(
            user_id=user_id,
            publish_request=request,
            access_token=access_token
        )
        
        logger.info(f"[SOCIAL] Post published by user {user_id}: {response.successful_posts}/{response.total_platforms} successful")
        
        return response
        
    except Exception as e:
        logger.error(f"[SOCIAL] Error publishing post: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error publishing post: {str(e)}"
        )


# ==================== Postiz Public API Proxy Routes ====================

@social_routes.get("/postiz/integrations")
async def postiz_list_integrations(
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    List Postiz integrations (connected channels) for the current user-business.
    Uses a user-scoped Postiz API key if available, or a global fallback key from env.
    """
    postiz_base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
    if not postiz_base_url:
        raise HTTPException(status_code=400, detail="POSTIZ_BASE_URL not configured")

    api_key = _resolve_postiz_api_key(user_id, db)
    if not api_key:
        raise HTTPException(
            status_code=404,
            detail="No Postiz API key found. Configure mapping or set POSTIZ_PUBLIC_API_KEY.",
        )

    try:
        client = PostizClient(postiz_base_url)
        return await client.list_integrations(api_key)
    except PostizAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@social_routes.post("/postiz/auto-login")
async def postiz_auto_login(
    user_id: str = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Build a Postiz LOCAL login payload for the current Autobus user and
    return the integrations URL so frontend can perform browser login + redirect.
    """
    postiz_base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
    if not postiz_base_url:
        raise HTTPException(status_code=400, detail="POSTIZ_BASE_URL not configured")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.email:
        raise HTTPException(status_code=404, detail="User/email not found")

    # Ensure org + API key exists for this user before auto-login redirect.
    try:
        _ = await _ensure_postiz_api_key(user_id, db)
    except Exception as ensure_error:
        logger.warning(f"[SOCIAL] Postiz provisioning check failed for user {user_id}: {ensure_error}")

    browser_postiz_url = (os.getenv("POSTIZ_PUBLIC_URL", "").strip() or postiz_base_url).rstrip("/")
    postiz_password = derive_postiz_password(
        user_id=user.id,
        email=user.email,
        autobus_password_hash=user.hashed_password,
    )

    login_payload = {
        "email": user.email,
        "password": postiz_password,
        "providerToken": "",
        "provider": "LOCAL",
    }

    # Optional server-side validation so caller can detect potential mismatch
    # (e.g. legacy users provisioned with old random password logic).
    postiz_login_ready = False
    try:
        await PostizClient(base_url=postiz_base_url).login_local(
            email=user.email,
            password=postiz_password,
        )
        postiz_login_ready = True
    except Exception as login_error:
        logger.warning(f"[SOCIAL] Postiz auto-login validation failed for user {user_id}: {login_error}")

    return {
        "postiz_login_ready": postiz_login_ready,
        "postiz_login": {
            "url": f"{browser_postiz_url}/api/auth/login",
            "body": login_payload,
        },
        "authorization_url": f"{browser_postiz_url}/integrations",
        "message": "Call postiz_login from browser, then redirect to authorization_url to link channels.",
    }


@social_routes.post("/postiz/posts")
async def postiz_create_post(
    payload: Dict[str, Any],
    agent_name: Optional[str] = Query(
        None,
        description=(
            "When set to digital_marketing (aliases: digital_margeting, digital-marketing), "
            "marketing text and media URLs from the request body are stored after Postiz accepts the post."
        ),
    ),
    jwt_subject: str = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Create/schedule a post in Postiz using the raw Postiz Public API payload.

    The payload is passed through to `POST /api/public/v1/posts` on your Postiz instance.

    For the digital marketing agent, pass `?agent_name=digital_marketing` so caption and media
    links are archived for later download via `/digital-marketing/assets`.
    """
    postiz_base_url = os.getenv("POSTIZ_BASE_URL", "").strip()
    if not postiz_base_url:
        raise HTTPException(status_code=400, detail="POSTIZ_BASE_URL not configured")

    api_key = _resolve_postiz_api_key(jwt_subject, db)
    if not api_key:
        raise HTTPException(
            status_code=404,
            detail="No Postiz API key found. Configure mapping or set POSTIZ_PUBLIC_API_KEY.",
        )

    try:
        client = PostizClient(postiz_base_url)
        result = await client.create_post(api_key, payload)
    except PostizAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    canonical_agent = normalize_digital_marketing_agent_name(agent_name)
    if canonical_agent:
        try:
            user = db.query(User).filter(User.email == jwt_subject).first()
            if user:
                text, links = extract_marketing_text_and_links(payload)
                DigitalMarketingAssetService(db).create_from_postiz(
                    user_internal_id=str(user.id),
                    agent_name=canonical_agent,
                    marketing_text=text,
                    content_links=links,
                    postiz_response=result if isinstance(result, dict) else {"value": result},
                )
        except Exception as arch_exc:
            logger.warning(
                "[DIGITAL_MARKETING] Failed to archive Postiz marketing payload: %s",
                arch_exc,
                exc_info=True,
            )

    return result


@social_routes.get(
    "/digital-marketing/assets",
    response_model=DigitalMarketingAssetListResponse,
)
async def list_digital_marketing_assets(
    jwt_subject: str = Depends(validate_token),
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    user = db.query(User).filter(User.email == jwt_subject).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    svc = DigitalMarketingAssetService(db)
    rows = svc.list_for_user(str(user.id), limit=limit, offset=offset)
    total = svc.count_for_user(str(user.id))
    items = [DigitalMarketingAssetResponse.model_validate(r) for r in rows]
    return DigitalMarketingAssetListResponse(items=items, total=total)


@social_routes.get(
    "/digital-marketing/assets/{asset_id}",
    response_model=DigitalMarketingAssetDetailResponse,
)
async def get_digital_marketing_asset(
    asset_id: str,
    jwt_subject: str = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == jwt_subject).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    row = DigitalMarketingAssetService(db).get_for_user(str(user.id), asset_id)
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    return DigitalMarketingAssetDetailResponse.model_validate(row)


@social_routes.get("/digital-marketing/assets/{asset_id}/download")
async def download_digital_marketing_asset(
    asset_id: str,
    jwt_subject: str = Depends(validate_token),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == jwt_subject).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    row = DigitalMarketingAssetService(db).get_for_user(str(user.id), asset_id)
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")

    body: Dict[str, Any] = {
        "id": row.id,
        "agent_name": row.agent_name,
        "marketing_text": row.marketing_text,
        "content_links": row.content_links or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "postiz_response": row.postiz_response,
    }
    safe_name = asset_id.replace("/", "_").replace("\\", "_")[:80]
    return JSONResponse(
        content=body,
        headers={
            "Content-Disposition": (
                f'attachment; filename="digital-marketing-{safe_name}.json"'
            )
        },
    )
