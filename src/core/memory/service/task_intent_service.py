from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.memory.service.due_date_parser import parse_due_at, parse_time_of_day
from core.memory.service.memory_service import MemoryService
from core.memory.service.reminder_delivery_service import ReminderDeliveryService

_DEFAULT_LIST_NAME = "Tasks"

_SCHEDULE_OPEN = frozenset({"open", "none", "no", "no_deadline", "someday"})
_SCHEDULE_DEADLINE = frozenset({"deadline", "one_time", "due", "once", "has_deadline"})
_SCHEDULE_RECURRING = frozenset({"recurring", "repeat", "repeating"})

_FREQ_MAP = {
    "daily": "DAILY",
    "day": "DAILY",
    "every day": "DAILY",
    "each day": "DAILY",
    "weekly": "WEEKLY",
    "week": "WEEKLY",
    "every week": "WEEKLY",
    "each week": "WEEKLY",
    "monthly": "MONTHLY",
    "month": "MONTHLY",
    "every month": "MONTHLY",
    "each month": "MONTHLY",
}

def normalize_schedule_type(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _SCHEDULE_OPEN:
        return "open"
    if value in _SCHEDULE_DEADLINE:
        return "deadline"
    if value in _SCHEDULE_RECURRING:
        return "recurring"
    if "recurr" in value or "repeat" in value:
        return "recurring"
    if "deadline" in value or "due" in value:
        return "deadline"
    if "open" in value or "no date" in value or "no deadline" in value:
        return "open"
    return None


def normalize_repeat_frequency(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _FREQ_MAP:
        return _FREQ_MAP[value]
    for key, freq in _FREQ_MAP.items():
        if key in value:
            return freq
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def next_recurrence_start(repeat_time: str, *, now: Optional[datetime] = None) -> datetime:
    now = _ensure_aware(now or _now())
    hour, minute = parse_time_of_day(repeat_time)
    candidate = datetime(
        now.year, now.month, now.day, hour, minute, tzinfo=timezone.utc
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class TaskIntentService:
    def __init__(self, db: Session):
        self.db = db
        self.memory = MemoryService(db)

    @staticmethod
    def _delivery_confirmation(delivery: dict) -> str:
        channels = [str(c).lower() for c in (delivery or {}).get("channels", [])]
        if "telegram" in channels and "sms" in channels:
            return "You'll get a Telegram and SMS reminder when it's due."
        if "telegram" in channels:
            return "You'll get a Telegram reminder when it's due."
        if "chat" in channels and "sms" in channels:
            return "You'll get a chat and SMS reminder when it's due."
        if "chat" in channels:
            return "You'll get a chat reminder when it's due."
        if "sms" in channels:
            return "You'll get an SMS reminder when it's due."
        return "You'll get a reminder when it's due."

    def create_from_slots(
        self,
        *,
        owner_user_id: str,
        slots: Dict[str, str],
        delivery: Optional[dict] = None,
    ) -> str:
        body = (slots.get("task_body") or "").strip()
        if not body:
            raise HTTPException(status_code=400, detail="Task description is required.")

        schedule = normalize_schedule_type(slots.get("schedule_type"))
        if not schedule and (slots.get("due_at") or "").strip():
            schedule = "deadline"
        reminder_delivery = delivery or ReminderDeliveryService.default_delivery_for_owner(owner_user_id)
        if schedule == "open":
            return self._create_open_task(owner_user_id=owner_user_id, body=body)
        if schedule == "deadline":
            due_raw = (slots.get("due_at") or "").strip()
            try:
                due_at = parse_due_at(due_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            self.memory.create_reminder(
                owner_user_id=owner_user_id,
                body=body,
                due_at=due_at,
                title=body[:200],
                delivery=reminder_delivery,
            )
            return f"✅ Reminder set for {self._format_user_datetime(due_at)}: {body}\n{self._delivery_confirmation(reminder_delivery)}"
        if schedule == "recurring":
            freq = normalize_repeat_frequency(slots.get("repeat_frequency"))
            if not freq:
                raise HTTPException(
                    status_code=400,
                    detail="Repeat frequency must be daily, weekly, or monthly.",
                )
            repeat_time_raw = (slots.get("repeat_time") or "").strip()
            try:
                due_at = next_recurrence_start(repeat_time_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            self.memory.create_reminder(
                owner_user_id=owner_user_id,
                body=body,
                due_at=due_at,
                title=body[:200],
                rrule=f"FREQ={freq}",
                delivery=reminder_delivery,
            )
            label = freq.lower()
            return (
                f"✅ Recurring task set ({label}, starting {self._format_user_datetime(due_at)}): {body}\n"
                f"{self._delivery_confirmation(reminder_delivery)}"
            )

        raise HTTPException(
            status_code=400,
            detail="Schedule type must be open, deadline, or recurring.",
        )

    def _create_open_task(self, *, owner_user_id: str, body: str) -> str:
        lst = self._get_or_create_default_list(owner_user_id=owner_user_id)
        self.memory.add_list_item(owner_user_id=owner_user_id, list_id=lst.id, text=body)
        return f"✅ Added to your task list ({lst.name}): {body}"

    def _get_or_create_default_list(self, *, owner_user_id: str):
        from core.memory.model.memory_list import MemoryList

        row = (
            self.db.query(MemoryList)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.name == _DEFAULT_LIST_NAME)
            .filter(MemoryList.deleted_at.is_(None))
            .first()
        )
        if row:
            return row
        return self.memory.create_list(
            owner_user_id=owner_user_id,
            name=_DEFAULT_LIST_NAME,
            description="Default task list",
        )

    @staticmethod
    def _format_user_datetime(dt: datetime) -> str:
        dt = _ensure_aware(dt)
        time_part = dt.strftime("%I:%M %p").lstrip("0")
        return f"{dt.strftime('%a %b %d')} at {time_part}"
