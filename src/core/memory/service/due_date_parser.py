"""Parse reminder due dates from natural language and structured inputs."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?",
    re.IGNORECASE,
)
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?",
)
_RELATIVE_RE = re.compile(
    r"\b(?:in|after)\s+(\d+)\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days)\b",
    re.IGNORECASE,
)
_NEXT_N_DAYS_RE = re.compile(
    r"\bnext\s+(\d+)\s+days?\b",
    re.IGNORECASE,
)
_WEEKDAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_WEEKDAY_RE = re.compile(
    r"\b(?:(?P<modifier>next|this|coming|upcoming)\s+)?"
    r"(?P<weekday>mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thu(?:rs?(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.IGNORECASE,
)
_TELEGRAM_DUE_PREFIX_RE = re.compile(r"^due:(?P<value>.+)$", re.IGNORECASE)


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


def _weekday_from_token(token: str) -> Optional[int]:
    return _WEEKDAY_NAMES.get((token or "").strip().lower())


def _this_weekday(base: date, weekday: int) -> date:
    """Nearest future calendar day with the given weekday (today counts if still ahead)."""
    days_ahead = (weekday - base.weekday()) % 7
    return base + timedelta(days=days_ahead)


def _next_weekday(base: date, weekday: int) -> date:
    """Weekday occurrence in the week after the nearest 'this' occurrence."""
    this = _this_weekday(base, weekday)
    if this == base:
        return this + timedelta(days=7)
    return this + timedelta(days=7)


def _parse_weekday_phrase(lowered: str, *, now: datetime) -> Optional[date]:
    match = _WEEKDAY_RE.search(lowered)
    if not match:
        return None

    weekday = _weekday_from_token(match.group("weekday"))
    if weekday is None:
        return None

    modifier = (match.group("modifier") or "").strip().lower()
    today = now.date()
    if modifier == "next":
        return _next_weekday(today, weekday)
    return _this_weekday(today, weekday)


def _parse_calendar_phrase(lowered: str, *, now: datetime) -> Optional[date]:
    today = now.date()

    if "day after tomorrow" in lowered:
        return today + timedelta(days=2)
    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    if "yesterday" in lowered:
        return today - timedelta(days=1)
    if "today" in lowered:
        return today

    next_days_match = _NEXT_N_DAYS_RE.search(lowered)
    if next_days_match:
        return today + timedelta(days=int(next_days_match.group(1)))

    if "next week" in lowered:
        return today + timedelta(days=7)
    if "next month" in lowered:
        month = today.month + 1
        year = today.year
        if month > 12:
            month = 1
            year += 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(today.day, last_day))

    weekday_date = _parse_weekday_phrase(lowered, now=now)
    if weekday_date is not None:
        return weekday_date

    return None


def _extract_time_fragment(lowered: str) -> str:
    at_match = re.search(r"\bat\s+(.+)$", lowered)
    if at_match:
        return at_match.group(1).strip()
    time_match = re.search(
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)",
        lowered,
        re.IGNORECASE,
    )
    if time_match:
        return time_match.group(0)
    return ""


def _combine_day_and_time(
    base_day: date,
    lowered: str,
    *,
    now: datetime,
    allow_same_day_bump: bool,
) -> datetime:
    hour, minute = 9, 0
    time_fragment = _extract_time_fragment(lowered)
    if time_fragment:
        hour, minute = parse_time_of_day(time_fragment)

    due = datetime(
        base_day.year,
        base_day.month,
        base_day.day,
        hour,
        minute,
        tzinfo=timezone.utc,
    )
    if allow_same_day_bump and due <= now:
        due += timedelta(days=1)
    return due


def parse_due_at(value: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse a due date/time from natural language or ISO-like strings."""
    now = _ensure_aware(now or datetime.now(timezone.utc))
    text = (value or "").strip()
    if not text:
        raise ValueError("Due date/time is required.")

    telegram_match = _TELEGRAM_DUE_PREFIX_RE.match(text)
    if telegram_match:
        text = telegram_match.group("value").strip()

    lowered = text.lower()

    relative_match = _RELATIVE_RE.search(lowered)
    if relative_match:
        qty = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        if unit.startswith("min"):
            return now + timedelta(minutes=qty)
        if unit.startswith("hour") or unit.startswith("hr"):
            return now + timedelta(hours=qty)
        if unit.startswith("day"):
            return now + timedelta(days=qty)

    if _ISO_RE.match(text):
        normalized = text.replace(" ", "T")
        if "T" not in normalized and len(normalized) == 10:
            normalized += "T09:00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
            return _ensure_aware(parsed)
        except ValueError as exc:
            raise ValueError(f"Could not parse date/time: {value}") from exc

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        base_day = date.fromisoformat(text)
        return _combine_day_and_time(
            base_day,
            lowered,
            now=now,
            allow_same_day_bump=False,
        )

    calendar_day = _parse_calendar_phrase(lowered, now=now)
    if calendar_day is not None:
        allow_bump = "tomorrow" not in lowered and "yesterday" not in lowered
        return _combine_day_and_time(
            calendar_day,
            lowered,
            now=now,
            allow_same_day_bump=allow_bump,
        )

    raise ValueError(
        f"Could not parse date/time: {value}. "
        "Try phrases like tomorrow at 3pm, Friday at 10am, next Thursday, in 2 days, or 2026-07-10 14:00."
    )
