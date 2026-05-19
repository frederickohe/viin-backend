import os
import re
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

import httpx


class PostizAPIError(RuntimeError):
    pass


def _normalize_postiz_email(email: str) -> str:
    """
    Postiz lowercases email on register but not on login; DB lookup is exact match.
    """
    return (email or "").strip().lower()


def _extract_session_jwt(res: httpx.Response) -> Optional[str]:
    """
    Postiz attaches the session JWT to the `auth` response header when NOT_SECURED is set,
    and/or to Set-Cookie. Autobus calls Postiz by Docker hostname (e.g. postiz:5000) while
    cookies may be scoped to the public domain — forward JWT explicitly on /api/user/self.
    """
    auth = res.headers.get("auth")
    if auth:
        return auth.strip()
    raw = getattr(res.headers, "raw", None) or []
    for key, value in raw:
        if key.lower() != b"set-cookie":
            continue
        text = value.decode("latin-1", errors="replace")
        m = re.search(r"(?i)\bauth=([^;]+)", text)
        if m:
            return unquote(m.group(1).strip().strip('"'))
    return None


def _auth_request_headers(jwt: Optional[str]) -> Dict[str, str]:
    if not jwt:
        return {}
    return {"auth": jwt}


def normalize_postiz_company(company: str, *, fallback: str = "Autobus Client") -> str:
    """
    Postiz `CreateOrgUserDto` requires company length 3–128 (class-validator).
    Autobus user fields can be shorter (e.g. two-letter brand); use a longer fallback.
    """
    name = (company or "").strip()
    if len(name) >= 3:
        return name[:128]
    fb = (fallback or "Autobus Client").strip()
    if len(fb) >= 3:
        return fb[:128]
    return "Org"[:128]


class PostizClient:
    """
    Minimal Postiz client for:
    - provisioning: POST /api/auth/register then GET /api/user/self
    - publishing: POST /api/public/v1/posts

    Notes:
    - For self-hosted Postiz, the public API base is `{POSTIZ_BASE_URL}/api/public/v1`.
    - The `/api/user/self` endpoint returns `orgId` and (for admins) `publicApi` (org API key).
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def provision_org_and_get_public_api_key(
        self,
        email: str,
        company: str,
        password: str,
        timeout_s: float = 20.0,
    ) -> Tuple[str, str]:
        """
        Creates a Postiz org + SUPERADMIN user via `/api/auth/register`,
        then calls `/api/user/self` to obtain:
        - organization id
        - organization public API key (used for `/api/public/v1/*`)
        """
        company_norm = normalize_postiz_company(company)
        email_norm = _normalize_postiz_email(email)
        session_jwt: Optional[str] = None
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
        ) as client:
            reg = await client.post(
                self._url("/api/auth/register"),
                json={
                    "provider": "LOCAL",
                    "email": email_norm,
                    "password": password,
                    "company": company_norm,
                },
            )
            # Postiz returns 400 with plain-text body for business errors (see auth.controller catch).
            # Duplicate email is 400 "Email already exists", not 409 — still recoverable via login.
            reg_ok = reg.status_code < 400
            duplicate_email = (
                reg.status_code == 400
                and "email already exists" in (reg.text or "").lower()
            )
            if not reg_ok and reg.status_code != 409 and not duplicate_email:
                raise PostizAPIError(
                    f"Postiz register failed ({reg.status_code}): {reg.text}"
                )

            if reg_ok:
                session_jwt = _extract_session_jwt(reg) or session_jwt

            me = await client.get(
                self._url("/api/user/self"),
                headers=_auth_request_headers(session_jwt),
            )
            if me.status_code == 401:
                # Some Postiz builds do not establish an authenticated session on register.
                login = await client.post(
                    self._url("/api/auth/login"),
                    json={
                        "provider": "LOCAL",
                        "email": email_norm,
                        "password": password,
                        "providerToken": "",
                    },
                )
                if login.status_code >= 400:
                    raise PostizAPIError(
                        f"Postiz login failed ({login.status_code}): {login.text}"
                    )
                session_jwt = _extract_session_jwt(login) or session_jwt
                me = await client.get(
                    self._url("/api/user/self"),
                    headers=_auth_request_headers(session_jwt),
                )

            if me.status_code >= 400:
                raise PostizAPIError(f"Postiz self failed ({me.status_code}): {me.text}")
            data = me.json()
            org_id = data.get("orgId") or data.get("organizationId") or data.get("id")
            public_api_key = data.get("publicApi") or data.get("apiKey")

            if not org_id or not public_api_key:
                raise PostizAPIError(
                    "Postiz self response missing orgId/publicApi; ensure registration succeeded and user has admin role."
                )

            return str(org_id), str(public_api_key)

    async def login_local(
        self,
        email: str,
        password: str,
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        """
        Login against Postiz LOCAL auth provider.
        Returns the response body, and raises for HTTP errors.
        """
        email_norm = _normalize_postiz_email(email)
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
        ) as client:
            res = await client.post(
                self._url("/api/auth/login"),
                json={
                    "provider": "LOCAL",
                    "email": email_norm,
                    "password": password,
                    "providerToken": "",
                },
            )
            if res.status_code >= 400:
                raise PostizAPIError(
                    f"Postiz login failed ({res.status_code}): {res.text}"
                )

            if not res.text.strip():
                return {}
            return res.json()

    async def create_post(
        self,
        public_api_key: str,
        payload: Dict[str, Any],
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            res = await client.post(
                self._url("/api/public/v1/posts"),
                headers={"Authorization": public_api_key, "Content-Type": "application/json"},
                json=payload,
            )
            if res.status_code >= 400:
                raise PostizAPIError(f"Postiz create post failed ({res.status_code}): {res.text}")
            return res.json()

    async def list_integrations(
        self,
        public_api_key: str,
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            res = await client.get(
                self._url("/api/public/v1/integrations"),
                headers={"Authorization": public_api_key},
            )
            if res.status_code >= 400:
                raise PostizAPIError(
                    f"Postiz list integrations failed ({res.status_code}): {res.text}"
                )
            return res.json()

    async def get_social_connect_url(
        self,
        public_api_key: str,
        integration: str,
        *,
        refresh: Optional[str] = None,
        timeout_s: float = 20.0,
    ) -> str:
        """
        OAuth URL for connecting a channel via Postiz Public API
        (`GET /api/public/v1/social/{integration}`).
        """
        slug = (integration or "").strip().lower()
        if not slug:
            raise PostizAPIError("integration slug is required")

        params: Dict[str, str] = {}
        if refresh:
            params["refresh"] = refresh.strip()

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            res = await client.get(
                self._url(f"/api/public/v1/social/{slug}"),
                headers={"Authorization": public_api_key},
                params=params or None,
            )
            if res.status_code >= 400:
                raise PostizAPIError(
                    f"Postiz social connect failed ({res.status_code}): {res.text}"
                )
            data = res.json() if res.text.strip() else {}
            if isinstance(data, dict):
                url = data.get("url") or data.get("authorization_url")
                if url:
                    return str(url).strip()
            raise PostizAPIError(
                "Postiz social connect response missing url; "
                "ensure FACEBOOK_APP_ID/SECRET are configured on Postiz."
            )


def postiz_enabled() -> bool:
    return bool(os.getenv("POSTIZ_BASE_URL", "").strip())


def generate_postiz_password(length: int = 28) -> str:
    # Strong random password; not stored (Postiz cookies/api key used instead).
    return uuid.uuid4().hex + uuid.uuid4().hex[: max(0, length - 32)]


def derive_postiz_password(*, username: str) -> str:
    """
    Postiz LOCAL sign-in password: Autobus username (``fullname``), not the Autobus
    login password. Email is used as the Postiz account identifier.
    """
    from utilities.integration_credentials import integration_local_password

    return integration_local_password(username=username)

