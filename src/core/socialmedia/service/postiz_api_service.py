import hashlib
import os
import uuid
from typing import Any, Dict, Tuple

import httpx


class PostizAPIError(RuntimeError):
    pass


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
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
        ) as client:
            reg = await client.post(
                self._url("/api/auth/register"),
                json={
                    "provider": "LOCAL",
                    "email": email,
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

            me = await client.get(self._url("/api/user/self"))
            if me.status_code == 401:
                # Some Postiz builds do not establish an authenticated session on register.
                login = await client.post(
                    self._url("/api/auth/login"),
                    json={
                        "provider": "LOCAL",
                        "email": email,
                        "password": password,
                        "providerToken": "",
                    },
                )
                if login.status_code >= 400:
                    raise PostizAPIError(
                        f"Postiz login failed ({login.status_code}): {login.text}"
                    )
                me = await client.get(self._url("/api/user/self"))

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
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
        ) as client:
            res = await client.post(
                self._url("/api/auth/login"),
                json={
                    "provider": "LOCAL",
                    "email": email,
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


def postiz_enabled() -> bool:
    return bool(os.getenv("POSTIZ_BASE_URL", "").strip())


def generate_postiz_password(length: int = 28) -> str:
    # Strong random password; not stored (Postiz cookies/api key used instead).
    return uuid.uuid4().hex + uuid.uuid4().hex[: max(0, length - 32)]


def derive_postiz_password(
    *,
    user_id: str,
    email: str,
    autobus_password_hash: str,
) -> str:
    """
    Deterministically derive a Postiz LOCAL password from Autobus user identity.
    This allows Backend/Frontend to generate the same password for Postiz register/login
    without storing Postiz plaintext credentials.
    """
    seed = f"{user_id}|{email.lower()}|{autobus_password_hash}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()

