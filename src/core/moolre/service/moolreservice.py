import logging
import re
from typing import Any, Dict

import httpx

from config import settings

logger = logging.getLogger(__name__)


class MoolreException(Exception):
    """Raised when Moolre API calls fail."""


class MoolreSMSService:
    """Send SMS via Moolre VAS API."""

    def __init__(self):
        self.base_url = settings.MOOLRE_API_URL.rstrip("/")
        self.api_user = settings.MOOLRE_API_USER
        self.vas_key = settings.MOOLRE_VAS_KEY
        self.sender_id = settings.MOOLRE_SENDER_ID

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        if phone is None:
            return ""
        raw = str(phone).strip()
        if not raw:
            return ""
        raw = re.sub(r"(?!^\+)[^\d]", "", raw)
        if raw.startswith("+"):
            raw = raw[1:]
        if raw.startswith("00"):
            raw = raw[2:]
        if raw.startswith("0") and len(raw) >= 2:
            raw = "233" + raw[1:]
        return raw

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_user:
            headers["X-API-USER"] = self.api_user
        if self.vas_key:
            headers["X-API-VASKEY"] = self.vas_key
        return headers

    def send_sms(self, phone: str, message: str) -> Dict[str, Any]:
        phone = self._normalize_phone(phone)
        if not phone:
            raise MoolreException("Phone number is missing or invalid")
        if not self.sender_id:
            raise MoolreException("Moolre sender ID is not configured")

        url = f"{self.base_url}/open/sms/send"
        payload = {
            "type": 1,
            "senderid": self.sender_id[:11],
            "messages": [{"recipient": phone, "message": message[:160]}],
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=payload, headers=self._headers())
            data = response.json()
            if response.is_success and data.get("status") == 1:
                return {
                    "success": True,
                    "msgid": f"moolre-{phone}",
                    "status": data.get("code", "SMS01"),
                    "raw_response": data,
                }
            msg = data.get("message", "Moolre SMS failed")
            logger.error("Moolre SMS error: %s", msg)
            return {"success": False, "error": msg, "raw_response": data}
        except httpx.RequestError as exc:
            logger.error("Moolre SMS request failed: %s", exc)
            raise MoolreException(f"SMS sending failed: {exc}") from exc

    def check_message_status(self, msgid: str) -> Dict[str, Any]:
        return {
            "success": True,
            "status": "SENT",
            "description": "Moolre SMS status polling not implemented",
            "message_id": msgid,
        }
