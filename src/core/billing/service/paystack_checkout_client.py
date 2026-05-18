import os
import secrets
import string
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

from core.billing.exceptions.billing_exceptions import BillingValidationException


class PaystackCheckoutResult:
    def __init__(
        self,
        reference: str,
        authorization_url: str,
        access_code: str,
    ):
        self.reference = reference
        self.authorization_url = authorization_url
        self.access_code = access_code


class PaystackCheckoutClient:
    """Low-level Paystack transaction/initialize client for billing checkout links."""

    def __init__(self) -> None:
        self.secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        self.base_url = "https://api.paystack.co"
        self.default_callback_url = os.getenv(
            "PAYSTACK_BILLING_CALLBACK_URL",
            os.getenv("PAYSTACK_CALLBACK_URL", ""),
        ).strip()

    def _headers(self) -> dict[str, str]:
        if not self.secret_key:
            raise BillingValidationException("PAYSTACK_SECRET_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def generate_reference() -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        return f"BILL-{timestamp}-{suffix}"

    @staticmethod
    def to_subunit(amount_major: float, currency: str) -> int:
        """Convert major currency units to Paystack subunit (pesewas/kobo/cents)."""
        currency = (currency or "GHS").upper()
        zero_decimal_currencies = {"JPY", "KRW", "XOF", "XAF"}
        if currency in zero_decimal_currencies:
            return int(round(amount_major))
        return int(round(amount_major * 100))

    def initialize_checkout_sync(
        self,
        email: str,
        amount_subunit: int,
        reference: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaystackCheckoutResult:
        ref = reference or self.generate_reference()
        payload: dict[str, Any] = {
            "email": email,
            "amount": amount_subunit,
            "reference": ref,
            "metadata": metadata or {},
        }

        resolved_callback = callback_url or self.default_callback_url
        if resolved_callback:
            payload["callback_url"] = resolved_callback

        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self.base_url}/transaction/initialize",
                    headers=self._headers(),
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Paystack API error: {exc.response.text}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Paystack service unavailable: {exc}",
            ) from exc

        if not result.get("status"):
            raise BillingValidationException(
                result.get("message", "Failed to initialize Paystack checkout")
            )

        data = result["data"]
        return PaystackCheckoutResult(
            reference=ref,
            authorization_url=data["authorization_url"],
            access_code=data["access_code"],
        )

    async def initialize_checkout(
        self,
        email: str,
        amount_subunit: int,
        reference: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaystackCheckoutResult:
        ref = reference or self.generate_reference()
        payload: dict[str, Any] = {
            "email": email,
            "amount": amount_subunit,
            "reference": ref,
            "metadata": metadata or {},
        }

        resolved_callback = callback_url or self.default_callback_url
        if resolved_callback:
            payload["callback_url"] = resolved_callback

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/transaction/initialize",
                    headers=self._headers(),
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Paystack API error: {exc.response.text}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Paystack service unavailable: {exc}",
            ) from exc

        if not result.get("status"):
            raise BillingValidationException(
                result.get("message", "Failed to initialize Paystack checkout")
            )

        data = result["data"]
        return PaystackCheckoutResult(
            reference=ref,
            authorization_url=data["authorization_url"],
            access_code=data["access_code"],
        )

    async def verify_transaction(self, reference: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/transaction/verify/{reference}",
                    headers=self._headers(),
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Paystack verification failed: {exc.response.text}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Paystack service unavailable: {exc}",
            ) from exc
