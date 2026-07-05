from __future__ import annotations

import json
from typing import Any


def extract_paystack_error_message(detail: Any) -> str:
    """Turn Paystack/HTTP errors into a short message for logs and users."""
    if detail is None:
        return ""
    text = str(detail).strip()
    if not text:
        return ""

    marker = "Paystack API error:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()

    if text.startswith("{"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict) and payload.get("message"):
                return str(payload["message"]).strip()
        except json.JSONDecodeError:
            pass
    return text


def format_paystack_user_message(detail: Any) -> str:
    """User-facing payment error with actionable hints when possible."""
    message = extract_paystack_error_message(detail)
    low = message.lower()

    if "ip address is not allowed" in low:
        return (
            "I couldn't open Paystack checkout because this server's IP address is not "
            "on your Paystack API whitelist. In the Paystack dashboard, go to "
            "Settings → API Keys & Webhooks and allow your server's IP (or turn off IP "
            "restrictions for testing)."
        )
    if "invalid key" in low or "invalid api key" in low:
        return (
            "Paystack is not configured correctly on the server (invalid secret key). "
            "Please check PAYSTACK_SECRET_KEY."
        )
    if "currency" in low:
        return f"I couldn't start Paystack checkout: {message}"
    if message:
        return f"I couldn't start Paystack checkout: {message}"
    return "I couldn't start the Paystack checkout right now. Please try again in a moment."
