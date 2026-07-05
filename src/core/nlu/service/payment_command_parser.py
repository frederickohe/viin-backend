"""Deterministic parser for obvious Paystack payment commands."""

from __future__ import annotations

import re
from typing import Dict, Optional

from utilities.phone_utils import extract_ghana_phone_numbers_from_text

_PAYMENT_VERB_RE = re.compile(
    r"^\s*(?:send|pay|transfer|give)\b",
    re.IGNORECASE,
)
_PAYMENT_AMOUNT_RE = re.compile(
    r"(?:send|pay|transfer|give)\s+(\d+(?:\.\d+)?)\s*(?:cedis?|ghs|gh\s*c|₵)?\b",
    re.IGNORECASE,
)
_MAKE_PAYMENT_AMOUNT_RE = re.compile(
    r"make\s+(?:a\s+)?payment\s+of\s+(\d+(?:\.\d+)?)\s*(?:cedis?|ghs|gh\s*c|₵)?\b",
    re.IGNORECASE,
)
_RECIPIENT_TAIL_RE = re.compile(r"\b(?:to|for)\s+(.+)$", re.IGNORECASE)
_RECIPIENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,48}[A-Za-z]$|^[A-Za-z]$")


def try_parse_payment_command(user_message: str) -> Optional[Dict[str, str]]:
    """
    Parse clear payment commands without LLM intent detection.

    Examples:
    - "send 2 cedis to Anna 0207926310"
    - "pay 50 cedis to John"
    - "make a payment of 25 GHS"
    """
    text = (user_message or "").strip()
    if not text:
        return None

    amount_match = _PAYMENT_AMOUNT_RE.search(text)
    if amount_match is None:
        amount_match = _MAKE_PAYMENT_AMOUNT_RE.search(text)
    if amount_match is None:
        return None

    if not _PAYMENT_VERB_RE.match(text) and _MAKE_PAYMENT_AMOUNT_RE.search(text) is None:
        return None

    slots: Dict[str, str] = {"amount": amount_match.group(1)}
    lower = text.lower()

    phones = extract_ghana_phone_numbers_from_text(text)
    if phones:
        slots["recipient_phone"] = phones[0]

    tail_match = _RECIPIENT_TAIL_RE.search(text)
    if tail_match:
        remainder = tail_match.group(1).strip()
        if phones:
            remainder = remainder.replace(phones[0], "", 1).strip()

        if " for " in remainder.lower():
            name_part, _, desc_part = remainder.partition(" for ")
            remainder = name_part.strip()
            desc = desc_part.strip()
            if desc:
                slots["description"] = desc

        remainder = re.sub(r"\b0\d{9}\b", "", remainder).strip()
        remainder = re.sub(
            r"\b(?:cedis?|ghs|gh\s*c|₵)\b",
            "",
            remainder,
            flags=re.IGNORECASE,
        ).strip()

        if remainder and _RECIPIENT_NAME_RE.match(remainder):
            slots["recipient_name"] = remainder

    return slots
