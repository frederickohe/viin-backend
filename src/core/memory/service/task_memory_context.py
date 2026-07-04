from __future__ import annotations

from sqlalchemy.orm import Session

from core.memory.service.briefing_service import BriefingPeriod, BriefingService, _now


class TaskMemoryContextService:
    """Build a compact task-memory summary from Postgres for LLM prompts."""

    def __init__(self, db: Session):
        self.db = db

    def build_context(self, *, owner_user_id: str) -> str:
        svc = BriefingService(self.db)
        tasks = svc.collect_tasks(owner_user_id=owner_user_id, period=BriefingPeriod.WEEKLY)
        if not tasks:
            return "No pending reminders, to-do list items, or saved notes."

        now = _now()
        lines = ["Pending reminders, to-dos, and notes for this user:"]
        for i, task in enumerate(tasks[:25], start=1):
            lines.append(f"{i}. {svc._task_detail(task, now=now)}")
        if len(tasks) > 25:
            lines.append(f"... and {len(tasks) - 25} more item(s).")
        return "\n".join(lines)
