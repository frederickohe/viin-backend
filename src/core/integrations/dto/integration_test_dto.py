from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class IntegrationProbeResult(BaseModel):
    configured: bool = False
    ok: bool = False
    http_status: Optional[int] = None
    detail: Optional[str] = None
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Small JSON subset from the upstream response when safe to include.",
    )


class ExternalHealthResponse(BaseModel):
    postiz: IntegrationProbeResult
    chatwoot: IntegrationProbeResult


class IntegrationSelfTestRequest(BaseModel):
    postiz: bool = Field(default=True, description="Run Postiz register + /api/user/self for the current user.")
    chatwoot: bool = Field(
        default=True,
        description="Run Chatwoot platform provision (account + user + link) for the current user.",
    )
    persist_db: bool = Field(
        default=False,
        description="When true, store Postiz / Chatwoot mappings like subscribe does. When false, return secrets once in the response only.",
    )


class IntegrationServiceResult(BaseModel):
    attempted: bool = False
    ok: bool = False
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    postiz_org_id: Optional[str] = None
    postiz_public_api_key: Optional[str] = Field(
        default=None,
        description="Present only when persist_db is false and provisioning succeeded.",
    )
    chatwoot_account_id: Optional[int] = None
    chatwoot_user_id: Optional[int] = None
    chatwoot_user_access_token: Optional[str] = Field(
        default=None,
        description="Present only when persist_db is false and provisioning succeeded.",
    )


class IntegrationSelfTestResponse(BaseModel):
    user_id: str
    persist_db: bool
    postiz: IntegrationServiceResult
    chatwoot: IntegrationServiceResult
