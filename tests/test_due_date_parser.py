from datetime import datetime, timezone

import pytest

from core.memory.service.due_date_parser import parse_due_at


def _utc(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def sunday_morning() -> datetime:
    return _utc(2026, 7, 5, 8, 23)


def test_coming_friday_from_sunday(sunday_morning: datetime) -> None:
    due = parse_due_at("coming friday", now=sunday_morning)
    assert due == _utc(2026, 7, 10, 9, 0)


def test_next_friday_from_sunday(sunday_morning: datetime) -> None:
    due = parse_due_at("next friday", now=sunday_morning)
    assert due == _utc(2026, 7, 17, 9, 0)


def test_friday_with_time(sunday_morning: datetime) -> None:
    due = parse_due_at("Friday at 10am", now=sunday_morning)
    assert due == _utc(2026, 7, 10, 10, 0)


def test_next_four_days(sunday_morning: datetime) -> None:
    due = parse_due_at("next 4 days", now=sunday_morning)
    assert due == _utc(2026, 7, 9, 9, 0)


def test_in_two_hours(sunday_morning: datetime) -> None:
    due = parse_due_at("in 2 hours", now=sunday_morning)
    assert due == _utc(2026, 7, 5, 10, 23)


def test_tomorrow_at_three_pm(sunday_morning: datetime) -> None:
    due = parse_due_at("tomorrow at 3pm", now=sunday_morning)
    assert due == _utc(2026, 7, 6, 15, 0)


def test_next_month(sunday_morning: datetime) -> None:
    due = parse_due_at("next month", now=sunday_morning)
    assert due == _utc(2026, 8, 5, 9, 0)


def test_telegram_callback_date(sunday_morning: datetime) -> None:
    due = parse_due_at("due:2026-07-11", now=sunday_morning)
    assert due == _utc(2026, 7, 11, 9, 0)


def test_iso_datetime(sunday_morning: datetime) -> None:
    due = parse_due_at("2026-07-10 14:00", now=sunday_morning)
    assert due == _utc(2026, 7, 10, 14, 0)


def test_unparseable_raises(sunday_morning: datetime) -> None:
    with pytest.raises(ValueError):
        parse_due_at("sometime soon", now=sunday_morning)
