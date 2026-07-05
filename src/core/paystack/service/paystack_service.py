import logging
import os

import httpx
import secrets
import string
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from datetime import datetime

from core.paystack.dto.request.paystack_request import PaystackInitializeRequest
from core.paystack.dto.response.paystack_response import PaystackInitializeResponse, PaystackVerifyResponse
from config import settings
from core.user.model.User import User
from core.paystack.model.transaction import Transaction  # You'll need to create this model
from utilities.uniqueidgenerator import UniqueIdGenerator

logger = logging.getLogger(__name__)

class PaystackService:
    def __init__(self, db: Session):
        self.db = db
        self.secret_key = (settings.PAYSTACK_SECRET_KEY or os.getenv("PAYSTACK_SECRET_KEY") or "").strip()
        self.currency = (os.getenv("PAYSTACK_CURRENCY", "GHS") or "GHS").strip().upper()
        self.base_url = "https://api.paystack.co"
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }

    def _ensure_configured(self) -> None:
        if not self.secret_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PAYSTACK_SECRET_KEY is not configured on the server.",
            )
    
    def generate_reference(self) -> str:
        """Generate a unique transaction reference"""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        return f"TX-{timestamp}-{random_str}"
    
    def _build_initialize_payload(
        self,
        *,
        user_id: str,
        user: User,
        request: PaystackInitializeRequest,
        reference: str,
    ) -> dict:
        payload = {
            "email": request.email,
            "amount": request.amount,
            "currency": self.currency,
            "reference": reference,
            "metadata": {
                "user_id": user_id,
                "user_email": user.email,
                **(request.metadata if request.metadata else {}),
            },
        }
        if request.callback_url:
            payload["callback_url"] = request.callback_url
        if request.channels:
            payload["channels"] = request.channels
        return payload

    def _persist_initialized_transaction(
        self,
        *,
        user_id: str,
        request: PaystackInitializeRequest,
        reference: str,
        payload: dict,
        result_data: dict,
    ) -> PaystackInitializeResponse:
        transaction = Transaction(
            id=str(UniqueIdGenerator.generate()),
            user_id=user_id,
            reference=reference,
            access_code=result_data["access_code"],
            amount=request.amount,
            email=request.email,
            status="pending",
            transaction_metadata=payload["metadata"],
            created_at=datetime.utcnow(),
        )
        self.db.add(transaction)
        self.db.commit()

        return PaystackInitializeResponse(
            status=True,
            message="Transaction initialized successfully",
            authorization_url=result_data["authorization_url"],
            access_code=result_data["access_code"],
            reference=reference,
        )

    def initialize_transaction_sync(
        self,
        user_id: str,
        request: PaystackInitializeRequest,
    ) -> PaystackInitializeResponse:
        """Initialize Paystack checkout from synchronous NLU handlers."""
        self._ensure_configured()
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        reference = request.reference or self.generate_reference()
        payload = self._build_initialize_payload(
            user_id=user_id,
            user=user,
            request=request,
            reference=reference,
        )

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.base_url}/transaction/initialize",
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()

            if result["status"]:
                try:
                    return self._persist_initialized_transaction(
                        user_id=user_id,
                        request=request,
                        reference=reference,
                        payload=payload,
                        result_data=result["data"],
                    )
                except Exception as exc:
                    logger.exception(
                        "Paystack initialized but DB persist failed ref=%s: %s",
                        reference,
                        exc,
                    )
                    data = result["data"]
                    return PaystackInitializeResponse(
                        status=True,
                        message="Transaction initialized successfully",
                        authorization_url=data["authorization_url"],
                        access_code=data["access_code"],
                        reference=reference,
                    )

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("message", "Failed to initialize transaction"),
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Paystack API error: {e.response.text}",
            ) from e
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Payment service unavailable: {str(e)}",
            ) from e

    async def initialize_transaction(
        self, 
        user_id: str,
        request: PaystackInitializeRequest
    ) -> PaystackInitializeResponse:
        """
        Initialize a Paystack transaction
        This is called from your backend to get a Paystack checkout URL.
        """
        self._ensure_configured()
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        reference = request.reference or self.generate_reference()
        payload = self._build_initialize_payload(
            user_id=user_id,
            user=user,
            request=request,
            reference=reference,
        )
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/transaction/initialize",
                    headers=self.headers,
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                
                if result["status"]:
                    try:
                        return self._persist_initialized_transaction(
                            user_id=user_id,
                            request=request,
                            reference=reference,
                            payload=payload,
                            result_data=result["data"],
                        )
                    except Exception as exc:
                        logger.exception(
                            "Paystack initialized but DB persist failed ref=%s: %s",
                            reference,
                            exc,
                        )
                        data = result["data"]
                        return PaystackInitializeResponse(
                            status=True,
                            message="Transaction initialized successfully",
                            authorization_url=data["authorization_url"],
                            access_code=data["access_code"],
                            reference=reference,
                        )

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result.get("message", "Failed to initialize transaction")
                )
                    
        except httpx.HTTPStatusError as e:
            error_detail = f"Paystack API error: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_detail
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Payment service unavailable: {str(e)}"
            )
    
    async def verify_transaction(self, reference: str) -> PaystackVerifyResponse:
        """
        Verify a transaction after payment is completed
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/transaction/verify/{reference}",
                    headers=self.headers,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                
                # Update transaction in database if it exists
                transaction = self.db.query(Transaction).filter(
                    Transaction.reference == reference
                ).first()
                
                if transaction and result["status"]:
                    transaction.status = result["data"]["status"]
                    transaction.paid_at = datetime.utcnow()
                    transaction.gateway_response = result["data"]["gateway_response"]
                    self.db.commit()
                
                return PaystackVerifyResponse(
                    status=result["status"],
                    message=result["message"],
                    data=result.get("data")
                )
                
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Verification failed: {e.response.text}"
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Payment service unavailable: {str(e)}"
            )
    
    async def list_banks(self, country: str = "nigeria") -> list:
        """Get list of banks for Paystack"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/bank?country={country}",
                    headers=self.headers,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                return result.get("data", [])
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to fetch banks: {str(e)}"
            )