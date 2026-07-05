"""Helpers for short Paystack payment confirmation follow-ups."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.nlu.service.payment_command_parser import try_parse_payment_command

_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|y|yeah|yep|yup|ok|okay|sure|confirm|confirmed|proceed|go ahead|do it|please do|affirmative)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"^\s*(?:no|n|nope|nah|cancel|stop|abort|don't|dont|never mind|nevermind)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_PAYMENT_CONFIRMATION_HINT_RE = re.compile(
    r"\b(?:please confirm this payment|reply\s+yes|paystack payment link|choose mobile money or bank)\b",
    re.IGNORECASE,
)


def is_affirmative_response(text: str) -> bool:
    return bool(_AFFIRMATIVE_RE.match((text or "").strip()))


def is_declining_response(text: str) -> bool:
    return bool(_DECLINE_RE.match((text or "").strip()))


def build_payment_confirmation_message(slots: Dict[str, str]) -> str:
    """Summarize a pending payment and ask the user to confirm."""
    raw_amount = (slots.get("amount") or "").strip()
    try:
        amount_display = f"{float(raw_amount):.2f}"
    except (TypeError, ValueError):
        amount_display = raw_amount or "?"

    recipient_name = (slots.get("recipient_name") or slots.get("recipient") or "").strip()
    recipient_phone = (slots.get("recipient_phone") or slots.get("phone_number") or "").strip()
    description = (slots.get("description") or "").strip()

    lines = ["💳 Please confirm this payment:", "", f"Amount: GHS {amount_display}"]
    if recipient_name or recipient_phone:
        parts = [p for p in (recipient_name, recipient_phone) if p]
        lines.append(f"Recipient: {' '.join(parts)}")
    if description:
        lines.append(f"Note: {description}")
    lines.append("")
    lines.append(
        "Reply yes to get your Paystack payment link — you'll choose Mobile Money or Bank there."
    )
    lines.append("Reply no to cancel.")
    return "\n".join(lines)


def conversation_mentions_payment_confirmation(text: str) -> bool:
    return bool(_PAYMENT_CONFIRMATION_HINT_RE.search(text or ""))


def _slots_have_amount(slots: Dict[str, str]) -> bool:
    raw = (slots or {}).get("amount")
    if raw is None or str(raw).strip() == "":
        return False
    try:
        return float(raw) > 0
    except (TypeError, ValueError):
        return False


def resolve_payment_slots(
    *,
    collected_slots: Optional[Dict[str, str]],
    pending_payment_dto: Optional[Dict],
    conversation_history: List[Dict],
) -> Optional[Dict[str, str]]:
    """Recover payment slots from state or recent user messages."""
    pending = pending_payment_dto or {}
    for source in (
        pending.get("slots"),
        collected_slots,
    ):
        if isinstance(source, dict) and _slots_have_amount(source):
            return {k: str(v) for k, v in source.items() if v is not None and str(v).strip()}

    for message in reversed(conversation_history or []):
        if message.get("role") != "user":
            continue
        parsed = try_parse_payment_command(message.get("content") or "")
        if parsed and _slots_have_amount(parsed):
            return parsed

    return None


def should_handle_payment_confirmation(
    *,
    user_message: str,
    current_intent: str,
    waiting_for_payment_confirmation: bool,
    collected_slots: Optional[Dict[str, str]],
    pending_payment_dto: Optional[Dict],
    conversation_history: List[Dict],
) -> bool:
    if not is_affirmative_response(user_message) and not is_declining_response(user_message):
        return False

    if waiting_for_payment_confirmation or current_intent == "make_payment":
        return True

    if resolve_payment_slots(
        collected_slots=collected_slots,
        pending_payment_dto=pending_payment_dto,
        conversation_history=conversation_history,
    ):
        last_assistant = next(
            (
                m.get("content") or ""
                for m in reversed(conversation_history or [])
                if m.get("role") == "assistant"
            ),
            "",
        )
        if conversation_mentions_payment_confirmation(last_assistant):
            return True

    return False
