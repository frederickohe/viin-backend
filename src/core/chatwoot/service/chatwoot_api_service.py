import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx


class ChatwootAPIError(RuntimeError):
    pass


class ChatwootClient:
    """
    Minimal Chatwoot Platform API client for provisioning:
    - POST /platform/api/v1/accounts
    - POST /platform/api/v1/users
    - POST /platform/api/v1/accounts/{account_id}/account_users
    """

    def __init__(self, base_url: str, platform_api_token: str):
        self.base_url = base_url.rstrip("/")
        self.platform_api_token = platform_api_token.strip()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self) -> Dict[str, str]:
        return {
            "api_access_token": self.platform_api_token,
            "Content-Type": "application/json",
        }

    async def create_account(
        self,
        *,
        name: str,
        support_email: Optional[str] = None,
        locale: str = "en",
        domain: Optional[str] = None,
        status: str = "active",
        timeout_s: float = 20.0,
    ) -> int:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            payload: Dict[str, Any] = {"name": name, "locale": locale, "status": status}
            if support_email:
                payload["support_email"] = support_email
            if domain:
                payload["domain"] = domain

            res = await client.post(
                self._url("/platform/api/v1/accounts"),
                headers=self._headers(),
                json=payload,
            )
            if res.status_code >= 400:
                raise ChatwootAPIError(
                    f"Chatwoot create account failed ({res.status_code}): {res.text}"
                )

            data = res.json() if res.text.strip() else {}
            account_id = data.get("id")
            if account_id is None:
                raise ChatwootAPIError("Chatwoot create account response missing 'id'")
            return int(account_id)

    async def create_user(
        self,
        *,
        name: str,
        email: str,
        password: str,
        display_name: Optional[str] = None,
        timeout_s: float = 20.0,
    ) -> Tuple[int, str]:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            payload: Dict[str, Any] = {
                "name": name,
                "email": email,
                "password": password,
            }
            if display_name:
                payload["display_name"] = display_name

            res = await client.post(
                self._url("/platform/api/v1/users"),
                headers=self._headers(),
                json=payload,
            )
            if res.status_code >= 400:
                raise ChatwootAPIError(
                    f"Chatwoot create user failed ({res.status_code}): {res.text}"
                )

            data = res.json() if res.text.strip() else {}
            user_id = data.get("id")
            access_token = data.get("access_token")
            if user_id is None or not access_token:
                raise ChatwootAPIError(
                    "Chatwoot create user response missing 'id' or 'access_token'"
                )
            return int(user_id), str(access_token)

    async def add_user_to_account(
        self,
        *,
        account_id: int,
        user_id: int,
        role: str = "administrator",
        timeout_s: float = 20.0,
    ) -> None:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            res = await client.post(
                self._url(f"/platform/api/v1/accounts/{int(account_id)}/account_users"),
                headers=self._headers(),
                json={"user_id": int(user_id), "role": role},
            )
            if res.status_code >= 400:
                raise ChatwootAPIError(
                    f"Chatwoot add account user failed ({res.status_code}): {res.text}"
                )

    async def provision_account_and_user(
        self,
        *,
        account_name: str,
        email: str,
        name: str,
        password: str,
        support_email: Optional[str] = None,
        domain: Optional[str] = None,
        role: str = "administrator",
        timeout_s: float = 20.0,
    ) -> Tuple[int, int, str]:
        account_id = await self.create_account(
            name=account_name,
            support_email=support_email or email,
            domain=domain,
            timeout_s=timeout_s,
        )
        user_id, access_token = await self.create_user(
            name=name,
            email=email,
            password=password,
            timeout_s=timeout_s,
        )
        await self.add_user_to_account(
            account_id=account_id,
            user_id=user_id,
            role=role,
            timeout_s=timeout_s,
        )
        return account_id, user_id, access_token

    async def list_accounts(
        self,
        *,
        page: int = 1,
        timeout_s: float = 15.0,
    ) -> Dict[str, Any]:
        """Platform API probe: list accounts (validates URL + platform token)."""
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            res = await client.get(
                self._url("/platform/api/v1/accounts"),
                headers=self._headers(),
                params={"page": int(page)},
            )
            if res.status_code >= 400:
                raise ChatwootAPIError(
                    f"Chatwoot list accounts failed ({res.status_code}): {res.text}"
                )
            return res.json() if res.text.strip() else {}


def chatwoot_enabled() -> bool:
    return bool(os.getenv("CHATWOOT_BASE_URL", "").strip()) and bool(
        os.getenv("CHATWOOT_PLATFORM_API_TOKEN", "").strip()
    )


class ChatwootAccountClient:
    """
    Minimal Chatwoot Application API client for an existing tenant account.

    Uses the *user* `api_access_token` (returned by platform user creation) to:
    - create/find inboxes
    - create/find contacts and contact inboxes
    - create conversations
    - post/list messages
    """

    def __init__(self, *, base_url: str, account_id: int, user_access_token: str):
        self.base_url = base_url.rstrip("/")
        self.account_id = int(account_id)
        self.user_access_token = user_access_token.strip()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self) -> Dict[str, str]:
        return {
            "api_access_token": self.user_access_token,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout_s: float = 20.0,
    ) -> Any:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            res = client.request(
                method,
                self._url(path),
                headers=self._headers(),
                json=json,
                params=params,
            )
        if res.status_code >= 400:
            raise ChatwootAPIError(f"Chatwoot API error ({res.status_code}): {res.text}")
        return res.json() if res.text.strip() else {}

    def list_inboxes(self, *, timeout_s: float = 20.0) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/inboxes",
            timeout_s=timeout_s,
        )
        payload = data.get("payload")
        return payload if isinstance(payload, list) else []

    def get_profile(self, *, timeout_s: float = 20.0) -> Dict[str, Any]:
        """Resolve the Chatwoot user behind this `api_access_token` (application API)."""
        return self._request("GET", "/api/v1/profile", timeout_s=timeout_s)

    def create_inbox_api_channel(
        self,
        *,
        name: str,
        enable_email_collect: bool = False,
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/inboxes/",
            json={
                "name": name,
                "enable_email_collect": enable_email_collect,
                "channel": {"type": "api"},
                # Ensure a contact maps to a single open thread by default.
                "lock_to_single_conversation": True,
                "allow_messages_after_resolved": True,
            },
            timeout_s=timeout_s,
        )

    def get_or_create_api_inbox_id(
        self, *, preferred_name: str = "Autobus API", timeout_s: float = 20.0
    ) -> int:
        for inbox in self.list_inboxes(timeout_s=timeout_s):
            if (inbox.get("name") or "").strip().lower() == preferred_name.strip().lower():
                inbox_id = inbox.get("id")
                if inbox_id is not None:
                    return int(inbox_id)
        created = self.create_inbox_api_channel(name=preferred_name, timeout_s=timeout_s)
        inbox_id = created.get("id")
        if inbox_id is None:
            raise ChatwootAPIError("Chatwoot create inbox response missing 'id'")
        return int(inbox_id)

    def search_contacts(self, *, q: str, timeout_s: float = 20.0) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/contacts/search",
            params={"q": q, "page": 1},
            timeout_s=timeout_s,
        )
        payload = data.get("payload")
        return payload if isinstance(payload, list) else []

    def create_contact(
        self,
        *,
        inbox_id: int,
        identifier: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "inbox_id": int(inbox_id),
            "identifier": identifier,
        }
        if name:
            payload["name"] = name
        if email:
            payload["email"] = email
        if phone_number:
            payload["phone_number"] = phone_number

        return self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/contacts",
            json=payload,
            timeout_s=timeout_s,
        )

    def create_contact_inbox(
        self, *, contact_id: int, inbox_id: int, source_id: str, timeout_s: float = 20.0
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/contacts/{int(contact_id)}/contact_inboxes",
            json={"inbox_id": int(inbox_id), "source_id": source_id},
            timeout_s=timeout_s,
        )

    def get_or_create_contact_for_inbox(
        self,
        *,
        inbox_id: int,
        identifier: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone_number: Optional[str] = None,
        timeout_s: float = 20.0,
    ) -> Tuple[int, str]:
        """
        Returns (contact_id, source_id_for_inbox).
        """
        # 1) Try search by identifier/email/phone.
        candidates = self.search_contacts(q=identifier, timeout_s=timeout_s)
        if not candidates and email:
            candidates = self.search_contacts(q=email, timeout_s=timeout_s)
        if not candidates and phone_number:
            candidates = self.search_contacts(q=phone_number, timeout_s=timeout_s)

        contact = candidates[0] if candidates else None
        if not contact:
            created = self.create_contact(
                inbox_id=inbox_id,
                identifier=identifier,
                name=name,
                email=email,
                phone_number=phone_number,
                timeout_s=timeout_s,
            )
            contact_id = created.get("id")
            if contact_id is None:
                raise ChatwootAPIError("Chatwoot create contact response missing 'id'")
            contact_inboxes = created.get("contact_inboxes") or []
            source_id = None
            for ci in contact_inboxes:
                inbox = (ci or {}).get("inbox") or {}
                if int(inbox.get("id") or 0) == int(inbox_id):
                    source_id = (ci or {}).get("source_id")
                    break
            if not source_id and contact_inboxes:
                source_id = (contact_inboxes[0] or {}).get("source_id")
            if not source_id:
                # Fallback: use identifier as source_id.
                source_id = identifier
            return int(contact_id), str(source_id)

        # Existing contact: find matching contact_inbox for this inbox_id
        contact_id = contact.get("id")
        if contact_id is None:
            raise ChatwootAPIError("Chatwoot contact search result missing 'id'")
        for ci in contact.get("contact_inboxes") or []:
            inbox = (ci or {}).get("inbox") or {}
            if int(inbox.get("id") or 0) == int(inbox_id):
                source_id = (ci or {}).get("source_id")
                if source_id:
                    return int(contact_id), str(source_id)

        # No inbox binding yet: create contact inbox with deterministic source_id
        source_id = identifier
        self.create_contact_inbox(
            contact_id=int(contact_id),
            inbox_id=int(inbox_id),
            source_id=source_id,
            timeout_s=timeout_s,
        )
        return int(contact_id), str(source_id)

    def create_conversation(
        self,
        *,
        inbox_id: int,
        source_id: str,
        contact_id: Optional[int] = None,
        timeout_s: float = 20.0,
    ) -> int:
        payload: Dict[str, Any] = {"inbox_id": int(inbox_id), "source_id": str(source_id)}
        if contact_id is not None:
            payload["contact_id"] = int(contact_id)
        data = self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations",
            json=payload,
            timeout_s=timeout_s,
        )
        conv_id = data.get("id")
        if conv_id is None:
            raise ChatwootAPIError("Chatwoot create conversation response missing 'id'")
        return int(conv_id)

    def create_message(
        self,
        *,
        conversation_id: int,
        content: str,
        message_type: str = "incoming",
        timeout_s: float = 20.0,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/accounts/{self.account_id}/conversations/{int(conversation_id)}/messages",
            json={"content": content, "message_type": message_type},
            timeout_s=timeout_s,
        )

    def list_messages(
        self, *, conversation_id: int, after_message_id: Optional[int] = None, timeout_s: float = 20.0
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if after_message_id is not None:
            params["after"] = int(after_message_id)
        data = self._request(
            "GET",
            f"/api/v1/accounts/{self.account_id}/conversations/{int(conversation_id)}/messages",
            params=params or None,
            timeout_s=timeout_s,
        )
        payload = data.get("payload")
        return payload if isinstance(payload, list) else []

    def send_and_wait_for_reply(
        self,
        *,
        inbox_id: int,
        contact_identifier: str,
        contact_name: Optional[str],
        contact_email: Optional[str],
        contact_phone: Optional[str],
        user_message: str,
        reply_timeout_s: float = 2.5,
        poll_interval_s: float = 0.5,
        timeout_s: float = 20.0,
    ) -> Optional[str]:
        """
        Sends an incoming message to Chatwoot and (optionally) waits briefly for an outgoing reply
        (agent/agent_bot). Returns reply content if observed, else None.
        """
        contact_id, source_id = self.get_or_create_contact_for_inbox(
            inbox_id=inbox_id,
            identifier=contact_identifier,
            name=contact_name,
            email=contact_email,
            phone_number=contact_phone,
            timeout_s=timeout_s,
        )
        conv_id = self.create_conversation(
            inbox_id=inbox_id,
            source_id=source_id,
            contact_id=contact_id,
            timeout_s=timeout_s,
        )
        created = self.create_message(
            conversation_id=conv_id,
            content=user_message,
            message_type="incoming",
            timeout_s=timeout_s,
        )
        after_id = created.get("id")
        after_id_int = int(after_id) if after_id is not None else None

        deadline = time.monotonic() + max(0.0, reply_timeout_s)
        while time.monotonic() < deadline:
            msgs = self.list_messages(
                conversation_id=conv_id, after_message_id=after_id_int, timeout_s=timeout_s
            )
            for m in msgs:
                # message_type is returned as int enum in list responses; also includes sender_type.
                sender_type = (m.get("sender_type") or "").strip().lower()
                content = (m.get("content") or "").strip()
                if sender_type in {"agent", "agent_bot"} and content:
                    return content
            time.sleep(poll_interval_s)
        return None


def derive_chatwoot_password(*, username: str) -> str:
    """
    Chatwoot sign-in password: Autobus username (``fullname``) plus a fixed suffix
    for Devise complexity rules. Email is used as the Chatwoot account identifier.
    """
    from utilities.integration_credentials import integration_chatwoot_password

    return integration_chatwoot_password(username=username)

