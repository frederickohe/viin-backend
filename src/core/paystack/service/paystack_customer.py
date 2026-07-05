from __future__ import annotations

from typing import Any, Dict, Optional

from core.user.model.User import User


def resolve_paystack_customer_email(
    *,
    user: Optional[User] = None,
    user_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Return an email for Paystack checkout without requiring users to manage one."""
    for raw in (
        (user_data or {}).get("email"),
        getattr(user, "email", None) if user else None,
    ):
        value = str(raw or "").strip()
        if value and "@" in value:
            return value

    phone = ""
    if user_data:
        phone = str(
            user_data.get("customer_phone") or user_data.get("user_id") or ""
        ).strip()
    if not phone and user:
        phone = str(user.phone or "").strip()

    digits = "".join(c for c in phone if c.isdigit())
    if digits:
        return f"{digits}@viin.paystack"

    account_id = str((user_data or {}).get("db_user_id") or "").strip()
    if not account_id and user:
        account_id = str(user.id or "").strip()
    if account_id:
        return f"{account_id}@viin.paystack"

    return "payments@viin.paystack"
