from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.memory.service.memory_service import MemoryService

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

_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?",
)


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


def parse_time_of_day(value: str) -> Tuple[int, int]:
    text = (value or "").strip().lower()
    match = _TIME_RE.search(text)
    if not match:
        raise ValueError(f"Could not understand the time: {value}")

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = (match.group("ampm") or "").replace(".", "")

    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid time: {value}")
    return hour, minute


def parse_due_at(value: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse a due date/time from natural language or ISO-like strings."""
    now = _ensure_aware(now or _now())
    text = (value or "").strip()
    if not text:
        raise ValueError("Due date/time is required.")

    lowered = text.lower()

    if _ISO_RE.match(text):
        normalized = text.replace(" ", "T")
        if "T" not in normalized and len(normalized) == 10:
            normalized += "T09:00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
            return _ensure_aware(parsed)
        except ValueError as exc:
            raise ValueError(f"Could not parse date/time: {value}") from exc

    if "tomorrow" in lowered:
        base_day = now.date() + timedelta(days=1)
    elif "yesterday" in lowered:
        base_day = now.date() - timedelta(days=1)
    elif "today" in lowered:
        base_day = now.date()
    else:
        base_day = now.date()

    hour, minute = 9, 0
    time_match = _TIME_RE.search(lowered)
    if time_match:
        hour, minute = parse_time_of_day(time_match.group(0))

    due = datetime(
        base_day.year,
        base_day.month,
        base_day.day,
        hour,
        minute,
        tzinfo=timezone.utc,
    )
    if due <= now and "tomorrow" not in lowered and "yesterday" not in lowered:
        due += timedelta(days=1)
    return due


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

    def create_from_slots(self, *, owner_user_id: str, slots: Dict[str, str]) -> str:
        body = (slots.get("task_body") or "").strip()
        if not body:
            raise HTTPException(status_code=400, detail="Task description is required.")

        schedule = normalize_schedule_type(slots.get("schedule_type"))
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
            )
            return f"✅ Reminder set for {self._format_user_datetime(due_at)}: {body}"
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
            )
            label = freq.lower()
            return (
                f"✅ Recurring task set ({label}, starting {self._format_user_datetime(due_at)}): {body}"
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
