"""Catalog of Viin product services users can enroll in at signup."""

from __future__ import annotations

from typing import Iterable, List, Optional

from core.user.model.User import User

VALID_SERVICES = frozenset({"assistant", "trading"})
DEFAULT_SERVICES: List[str] = ["assistant"]


def normalize_services(services: Optional[Iterable[str]]) -> List[str]:
    """Return a de-duplicated list of valid service ids. Defaults to assistant."""
    if not services:
        return list(DEFAULT_SERVICES)

    out: List[str] = []
    for raw in services:
        if raw is None:
            continue
        key = str(raw).strip().lower()
        if key in VALID_SERVICES and key not in out:
            out.append(key)
    return out or list(DEFAULT_SERVICES)


def services_from_user(user: User) -> List[str]:
    """Read enrolled services from the user row (legacy users → assistant)."""
    stored = getattr(user, "services", None)
    if isinstance(stored, list) and stored:
        return normalize_services(stored)
    return list(DEFAULT_SERVICES)


def user_has_service(user: User, service: str) -> bool:
    return service in services_from_user(user)


def merge_services(existing: Optional[Iterable[str]], additions: Iterable[str]) -> List[str]:
    return normalize_services([*(existing or []), *additions])
