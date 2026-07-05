from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy.orm import Session

from core.memory.model.memory_list import MemoryList, MemoryListItem
from core.memory.model.memory_enums import MemoryItemType, ReminderStatus
from core.memory.model.memory_item import MemoryItem
from core.memory.model.reminder import Reminder


class BriefingPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


_URGENT_PATTERN = re.compile(
    r"\b(urgent|asap|a\.s\.a\.p|immediately|critical|important|high[\s-]?priority)\b",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class BriefingTask:
    title: str
    source: str  # "reminder" | "todo" | "note"
    due_at: Optional[datetime]
    list_name: Optional[str]
    is_overdue: bool
    has_urgent_keyword: bool
    sort_key: tuple
    entity_id: str
    list_id: Optional[str] = None


_BRIEFING_NOTE_TYPES = {
    MemoryItemType.NOTE,
    MemoryItemType.MESSAGE,
    MemoryItemType.QUOTE,
}


class BriefingService:
    def __init__(self, db: Session):
        self.db = db

    def build_briefing(self, *, owner_user_id: str, period: BriefingPeriod) -> str:
        tasks = self.collect_tasks(owner_user_id=owner_user_id, period=period)
        return self.format_briefing(tasks=tasks, period=period)

    def build_due_day_briefing(self, *, owner_user_id: str, day_offset: int = -1) -> str:
        """List reminders that were due on a specific day (default: yesterday)."""
        tasks = self.collect_tasks_due_on_day(owner_user_id=owner_user_id, day_offset=day_offset)
        now = _now()
        target_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
        if day_offset == -1:
            label = "Yesterday"
        elif day_offset == 0:
            label = "Today"
        elif day_offset == 1:
            label = "Tomorrow"
        else:
            label = target_day.strftime("%A, %B %d")

        date_str = target_day.strftime("%A, %B %d, %Y")
        if not tasks:
            return (
                f"📋 {label} — {date_str}\n\n"
                f"No reminders were due {label.lower()}."
            )

        lines = [f"📋 {label} — {date_str}", ""]
        lines.append(
            f"You had {len(tasks)} reminder{'s' if len(tasks) != 1 else ''} due {label.lower()}:"
        )
        lines.append("")
        for i, task in enumerate(tasks, start=1):
            lines.append(f"{i}. {self._task_detail(task, now=now)}")
        lines.append("")
        lines.append(
            'To remove an item, say "delete 1" or "remove 2" using the number from the list above.'
        )
        return "\n".join(lines)

    def collect_tasks_due_on_day(
        self, *, owner_user_id: str, day_offset: int
    ) -> List[BriefingTask]:
        now = _now()
        target_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
        day_end = target_day + timedelta(days=1)
        tasks: List[BriefingTask] = []

        reminders = (
            self.db.query(Reminder)
            .filter(Reminder.owner_user_id == owner_user_id)
            .filter(Reminder.status.in_((ReminderStatus.SCHEDULED, ReminderStatus.SENT, ReminderStatus.FAILED)))
            .filter(Reminder.due_at >= target_day)
            .filter(Reminder.due_at < day_end)
            .order_by(Reminder.due_at.asc())
            .all()
        )
        for r in reminders:
            due = _ensure_aware(r.due_at)
            label = (r.title or r.body or "Reminder").strip()
            tasks.append(
                BriefingTask(
                    title=label,
                    source="reminder",
                    due_at=due,
                    list_name=None,
                    is_overdue=due < now,
                    has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                    sort_key=self._sort_key(
                        is_overdue=due < now,
                        has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                        due_at=due,
                        created_at=r.created_at,
                    ),
                    entity_id=r.id,
                )
            )

        tasks.sort(key=lambda t: t.sort_key)
        return tasks

    def collect_tasks(self, *, owner_user_id: str, period: BriefingPeriod) -> List[BriefingTask]:
        now = _now()
        window_end = self._window_end(now, period)
        tasks: List[BriefingTask] = []

        reminders = (
            self.db.query(Reminder)
            .filter(Reminder.owner_user_id == owner_user_id)
            .filter(Reminder.status.in_((ReminderStatus.SCHEDULED, ReminderStatus.SENT, ReminderStatus.FAILED)))
            .order_by(Reminder.due_at.asc())
            .all()
        )
        for r in reminders:
            due = _ensure_aware(r.due_at)
            is_overdue = due < now
            if not is_overdue and due > window_end:
                continue
            label = (r.title or r.body or "Reminder").strip()
            tasks.append(
                BriefingTask(
                    title=label,
                    source="reminder",
                    due_at=due,
                    list_name=None,
                    is_overdue=is_overdue,
                    has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                    sort_key=self._sort_key(
                        is_overdue=is_overdue,
                        has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                        due_at=due,
                        created_at=r.created_at,
                    ),
                    entity_id=r.id,
                )
            )

        open_items = (
            self.db.query(MemoryListItem, MemoryList)
            .join(MemoryList, MemoryList.id == MemoryListItem.list_id)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.deleted_at.is_(None))
            .filter(MemoryListItem.deleted_at.is_(None))
            .filter(MemoryListItem.completed_at.is_(None))
            .order_by(MemoryListItem.created_at.asc())
            .all()
        )
        for item, lst in open_items:
            text = (item.text or "").strip()
            if not text:
                continue
            tasks.append(
                BriefingTask(
                    title=text,
                    source="todo",
                    due_at=None,
                    list_name=lst.name,
                    is_overdue=False,
                    has_urgent_keyword=bool(_URGENT_PATTERN.search(text)),
                    sort_key=self._sort_key(
                        is_overdue=False,
                        has_urgent_keyword=bool(_URGENT_PATTERN.search(text)),
                        due_at=None,
                        created_at=item.created_at,
                    ),
                    entity_id=item.id,
                    list_id=lst.id,
                )
            )

        memory_items = (
            self.db.query(MemoryItem)
            .filter(MemoryItem.owner_user_id == owner_user_id)
            .filter(MemoryItem.deleted_at.is_(None))
            .filter(MemoryItem.item_type.in_(tuple(_BRIEFING_NOTE_TYPES)))
            .order_by(MemoryItem.created_at.asc())
            .all()
        )
        for mem in memory_items:
            label = (mem.title or mem.text or "").strip()
            if not label:
                continue
            if mem.title and mem.text and mem.text.strip() != label:
                label = f"{mem.title.strip()}: {mem.text.strip()}"
            tasks.append(
                BriefingTask(
                    title=label,
                    source="note",
                    due_at=None,
                    list_name=None,
                    is_overdue=False,
                    has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                    sort_key=self._sort_key(
                        is_overdue=False,
                        has_urgent_keyword=bool(_URGENT_PATTERN.search(label)),
                        due_at=None,
                        created_at=mem.created_at,
                    ),
                    entity_id=mem.id,
                )
            )

        tasks.sort(key=lambda t: t.sort_key)
        return tasks

    @staticmethod
    def _window_end(now: datetime, period: BriefingPeriod) -> datetime:
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == BriefingPeriod.DAILY:
            return start_of_today + timedelta(days=1)
        return start_of_today + timedelta(days=7)

    @staticmethod
    def _sort_key(
        *,
        is_overdue: bool,
        has_urgent_keyword: bool,
        due_at: Optional[datetime],
        created_at: Optional[datetime],
    ) -> tuple:
        due_sort = due_at.timestamp() if due_at else float("inf")
        age_sort = created_at.timestamp() if created_at else float("inf")
        return (
            0 if is_overdue else 1,
            0 if has_urgent_keyword else 1,
            due_sort,
            age_sort,
        )

    def format_briefing(self, *, tasks: List[BriefingTask], period: BriefingPeriod) -> str:
        now = _now()
        label = "Daily" if period == BriefingPeriod.DAILY else "Weekly"
        date_str = now.strftime("%A, %B %d, %Y")

        if not tasks:
            return (
                f"📋 {label} Briefing — {date_str}\n\n"
                "You're all caught up — no pending to-dos, reminders, or saved notes "
                f"for this {'day' if period == BriefingPeriod.DAILY else 'week'}."
            )

        lines = [f"📋 {label} Briefing — {date_str}", ""]
        scope = "today" if period == BriefingPeriod.DAILY else "the next 7 days"
        lines.append(f"You have {len(tasks)} item{'s' if len(tasks) != 1 else ''} to focus on ({scope}):")
        lines.append("")

        for i, task in enumerate(tasks, start=1):
            detail = self._task_detail(task, now=now)
            lines.append(f"{i}. {detail}")

        lines.append("")
        lines.append(
            "Most pressing item is listed first. To remove an item, say "
            '"delete 1" or "remove 2" using the number from the list above.'
        )
        return "\n".join(lines)

    @staticmethod
    def tasks_to_refs(tasks: List[BriefingTask]) -> List[dict]:
        refs: List[dict] = []
        for task in tasks:
            ref = {
                "source": task.source,
                "entity_id": task.entity_id,
                "title": task.title,
            }
            if task.list_id:
                ref["list_id"] = task.list_id
            refs.append(ref)
        return refs

    def delete_task_at_index(
        self,
        *,
        owner_user_id: str,
        task_refs: List[dict],
        index: int,
    ) -> str:
        if not task_refs:
            raise ValueError(
                "No briefing list to work from. Ask for a daily or weekly briefing first."
            )
        if index < 1 or index > len(task_refs):
            raise ValueError(
                f"Please choose a number between 1 and {len(task_refs)} from your last briefing."
            )

        ref = task_refs[index - 1]
        title = (ref.get("title") or "that item").strip()
        source = ref.get("source")
        entity_id = ref.get("entity_id")

        from core.memory.service.memory_service import MemoryService

        memory = MemoryService(self.db)
        if source == "reminder":
            memory.cancel_reminder(owner_user_id=owner_user_id, reminder_id=entity_id)
            return f"✅ Removed reminder: {title}"
        if source == "todo":
            list_id = ref.get("list_id")
            if not list_id:
                raise ValueError("Could not find that to-do item.")
            memory.delete_list_item(
                owner_user_id=owner_user_id,
                list_id=list_id,
                item_id=entity_id,
            )
            return f"✅ Removed from your task list: {title}"
        if source == "note":
            memory.delete_memory_item(owner_user_id=owner_user_id, item_id=entity_id)
            return f"✅ Removed saved note: {title}"

        raise ValueError("That item type cannot be removed from chat.")

    @staticmethod
    def _task_detail(task: BriefingTask, *, now: datetime) -> str:
        parts: List[str] = [task.title]

        if task.source == "reminder" and task.due_at:
            due = _ensure_aware(task.due_at)
            if task.is_overdue:
                parts.append(f"(overdue — was due {BriefingService._format_due(due, now)})")
            else:
                parts.append(f"(due {BriefingService._format_due(due, now)})")
        elif task.list_name:
            parts.append(f"(from list: {task.list_name})")
        elif task.source == "note":
            parts.append("(saved note)")

        if task.has_urgent_keyword:
            parts.append("[marked urgent]")

        return " ".join(parts)

    @staticmethod
    def _format_due(due: datetime, now: datetime) -> str:
        due = _ensure_aware(due)
        now = _ensure_aware(now)
        due_day = due.replace(hour=0, minute=0, second=0, microsecond=0)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        delta_days = (due_day - today).days

        time_part = due.strftime("%I:%M %p").lstrip("0")
        if delta_days == 0:
            return f"today at {time_part}"
        if delta_days == 1:
            return f"tomorrow at {time_part}"
        if delta_days == -1:
            return f"yesterday at {time_part}"
        if delta_days < -1:
            return f"{abs(delta_days)} days ago"
        if delta_days <= 7:
            return f"{due.strftime('%A')} at {time_part}"
        return due.strftime("%b %d at %I:%M %p").lstrip("0")
