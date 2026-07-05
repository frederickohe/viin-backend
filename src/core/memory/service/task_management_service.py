from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from core.memory.service.briefing_service import BriefingService, BriefingTask, _now
from core.memory.service.memory_service import MemoryService
from core.memory.service.task_intent_service import parse_due_at

_DUE_PREFIX_RE = re.compile(r"^due\s+", re.IGNORECASE)


def parse_task_number(raw: object) -> int:
    text = str(raw or "").strip().upper()
    if text.startswith("T"):
        text = text[1:].strip()
    if not text.isdigit():
        raise ValueError("Task ID must be a number like 1 or T1.")
    return int(text)


def make_ref_id(index: int) -> str:
    return f"T{index}"


class TaskManagementService:
    def __init__(self, db: Session):
        self.db = db
        self.briefing = BriefingService(db)

    def list_manageable_tasks(self, *, owner_user_id: str) -> List[BriefingTask]:
        return self.briefing.collect_all_tasks(owner_user_id=owner_user_id)

    def format_manage_list(self, *, tasks: List[BriefingTask]) -> str:
        now = _now()
        date_str = now.strftime("%A, %B %d, %Y")

        if not tasks:
            return (
                f"📝 Manage Tasks — {date_str}\n\n"
                "You don't have any tasks, reminders, or saved notes to manage right now."
            )

        lines = [
            f"📝 Manage Tasks — {date_str}",
            "",
            f"{len(tasks)} item{'s' if len(tasks) != 1 else ''} — each has an ID you can reference:",
            "",
        ]
        for i, task in enumerate(tasks, start=1):
            ref_id = make_ref_id(i)
            detail = self.briefing._task_detail(task, now=now)
            lines.append(f"{ref_id}. {detail}")

        lines.append("")
        lines.append("Examples:")
        lines.append('• Delete: "delete T1" or "delete 2"')
        lines.append('• Update text: "update T1 to buy eggs"')
        lines.append('• Update due date: "update T2 due tomorrow at 3pm"')
        return "\n".join(lines)

    @staticmethod
    def tasks_to_refs(tasks: List[BriefingTask]) -> List[dict]:
        refs: List[dict] = []
        for i, task in enumerate(tasks, start=1):
            ref = {
                "ref_id": make_ref_id(i),
                "index": i,
                "source": task.source,
                "entity_id": task.entity_id,
                "title": task.title,
            }
            if task.list_id:
                ref["list_id"] = task.list_id
            refs.append(ref)
        return refs

    @staticmethod
    def _resolve_ref(task_refs: List[dict], index: int) -> dict:
        if not task_refs:
            raise ValueError(
                'Say "manage tasks" first to see your items with IDs (T1, T2, …).'
            )
        if index < 1 or index > len(task_refs):
            raise ValueError(
                f"Please choose an ID between T1 and T{len(task_refs)} from your manage list."
            )
        return task_refs[index - 1]

    def delete_task_at_index(
        self,
        *,
        owner_user_id: str,
        task_refs: List[dict],
        index: int,
    ) -> str:
        ref = self._resolve_ref(task_refs, index)
        title = (ref.get("title") or "that item").strip()
        source = ref.get("source")
        entity_id = ref.get("entity_id")
        ref_id = ref.get("ref_id") or make_ref_id(index)

        memory = MemoryService(self.db)
        if source == "reminder":
            memory.cancel_reminder(owner_user_id=owner_user_id, reminder_id=entity_id)
            return f"✅ Deleted {ref_id}: {title}"
        if source == "todo":
            list_id = ref.get("list_id")
            if not list_id:
                raise ValueError("Could not find that to-do item.")
            memory.delete_list_item(
                owner_user_id=owner_user_id,
                list_id=list_id,
                item_id=entity_id,
            )
            return f"✅ Deleted {ref_id}: {title}"
        if source == "note":
            memory.delete_memory_item(owner_user_id=owner_user_id, item_id=entity_id)
            return f"✅ Deleted {ref_id}: {title}"

        raise ValueError("That item type cannot be removed from chat.")

    def update_task_at_index(
        self,
        *,
        owner_user_id: str,
        task_refs: List[dict],
        index: int,
        task_body: Optional[str] = None,
        due_at_raw: Optional[str] = None,
    ) -> str:
        ref = self._resolve_ref(task_refs, index)
        body = (task_body or "").strip()
        due_raw = (due_at_raw or "").strip()
        if not body and not due_raw:
            raise ValueError("Tell me what to change — a new description or a new due date.")

        source = ref.get("source")
        entity_id = ref.get("entity_id")
        ref_id = ref.get("ref_id") or make_ref_id(index)
        memory = MemoryService(self.db)
        changes: List[str] = []

        if source == "reminder":
            due_at: Optional[datetime] = None
            if due_raw:
                due_at = parse_due_at(due_raw)
            memory.update_reminder(
                owner_user_id=owner_user_id,
                reminder_id=entity_id,
                body=body or None,
                due_at=due_at,
            )
            if body:
                changes.append(f"text to \"{body}\"")
            if due_at:
                changes.append(f"due date to {self.briefing._format_due(due_at, _now())}")
        elif source == "todo":
            if due_raw:
                raise ValueError(
                    f"{ref_id} is an open to-do without a due date. "
                    "Update the text, or delete it and add a reminder with a deadline."
                )
            if not body:
                raise ValueError(f"What should {ref_id} say?")
            memory.update_list_item(
                owner_user_id=owner_user_id,
                list_id=ref["list_id"],
                item_id=entity_id,
                text=body,
            )
            changes.append(f"text to \"{body}\"")
        elif source == "note":
            if due_raw:
                raise ValueError(f"{ref_id} is a saved note and does not have a due date.")
            if not body:
                raise ValueError(f"What should {ref_id} say?")
            memory.update_memory_item(
                owner_user_id=owner_user_id,
                item_id=entity_id,
                text=body,
            )
            changes.append(f"text to \"{body}\"")
        else:
            raise ValueError("That item type cannot be updated from chat.")

        joined = " and ".join(changes)
        return f"✅ Updated {ref_id}: {joined}"

    @staticmethod
    def parse_update_payload(text: str) -> Dict[str, str]:
        """Split quick-update remainder into task_body or due_at."""
        value = (text or "").strip()
        if _DUE_PREFIX_RE.match(value):
            return {"due_at": _DUE_PREFIX_RE.sub("", value, count=1).strip()}
        return {"task_body": value}
