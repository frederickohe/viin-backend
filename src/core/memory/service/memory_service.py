from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.memory.model.memory_enums import MemoryItemType, MemoryVisibility
from core.memory.model.memory_item import MemoryItem
from core.memory.model.memory_list import MemoryList, MemoryListItem
from core.memory.model.memory_enums import ReminderStatus
from core.memory.model.reminder import Reminder
from core.memory.model.share_grant import MemoryShareGrant
from core.memory.model.source_message import SourceMessage
from core.user.model.User import User

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService:
    def __init__(self, db: Session):
        self.db = db

    # -----------------------
    # Source messages (raw)
    # -----------------------

    def create_source_message(
        self,
        *,
        user_id: str,
        channel: str,
        text: Optional[str],
        payload: Optional[dict],
        external_message_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> SourceMessage:
        msg = SourceMessage(
            id=uuid.uuid4().hex,
            user_id=user_id,
            channel=channel,
            external_message_id=external_message_id,
            conversation_id=conversation_id,
            text=text,
            payload=payload or {},
            created_at=_now(),
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg

    # -----------------------
    # Memory items
    # -----------------------

    def create_memory_item(
        self,
        *,
        owner_user_id: str,
        item_type: MemoryItemType,
        title: Optional[str] = None,
        text: Optional[str] = None,
        url: Optional[str] = None,
        file_id: Optional[str] = None,
        tags: Optional[dict] = None,
        metadata: Optional[dict] = None,
        visibility: MemoryVisibility = MemoryVisibility.PRIVATE,
        source_message_id: Optional[str] = None,
        index: bool = True,
    ) -> MemoryItem:
        owner = self.db.query(User).filter(User.id == owner_user_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="User not found")

        item = MemoryItem(
            id=uuid.uuid4().hex,
            owner_user_id=owner_user_id,
            visibility=visibility,
            item_type=item_type,
            title=title,
            text=text,
            url=url,
            file_id=file_id,
            tags=tags or {},
            item_metadata=metadata or {},
            source_message_id=source_message_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def get_memory_item(self, *, owner_user_id: str, item_id: str) -> MemoryItem:
        item = (
            self.db.query(MemoryItem)
            .filter(MemoryItem.id == item_id)
            .filter(MemoryItem.owner_user_id == owner_user_id)
            .first()
        )
        if not item or item.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Memory item not found")
        return item

    def list_memory_items(self, *, owner_user_id: str, limit: int = 50) -> List[MemoryItem]:
        q = (
            self.db.query(MemoryItem)
            .filter(MemoryItem.owner_user_id == owner_user_id)
            .filter(MemoryItem.deleted_at.is_(None))
            .order_by(MemoryItem.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        return list(q.all())

    def delete_memory_item(self, *, owner_user_id: str, item_id: str) -> None:
        item = self.get_memory_item(owner_user_id=owner_user_id, item_id=item_id)
        item.deleted_at = _now()
        self.db.add(item)
        self.db.commit()

    def search_memory(self, *, owner_user_id: str, query: str, limit: int = 10) -> Dict[str, Any]:
        needle = (query or "").strip()
        if not needle:
            return {"hits": [], "items": []}

        pattern = f"%{needle}%"
        rows = (
            self.db.query(MemoryItem)
            .filter(MemoryItem.owner_user_id == owner_user_id)
            .filter(MemoryItem.deleted_at.is_(None))
            .filter(
                or_(
                    MemoryItem.title.ilike(pattern),
                    MemoryItem.text.ilike(pattern),
                    MemoryItem.url.ilike(pattern),
                )
            )
            .order_by(MemoryItem.updated_at.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        hits = [
            {
                "score": 1.0,
                "text": " ".join(
                    p for p in [(row.title or "").strip(), (row.text or "").strip()] if p
                ),
                "payload": {"metadata": {"source": "memory_item", "memory_item_id": row.id}},
            }
            for row in rows
        ]
        return {"hits": hits, "items": rows}

    # -----------------------
    # Lists
    # -----------------------

    def create_list(
        self, *, owner_user_id: str, name: str, description: Optional[str] = None
    ) -> MemoryList:
        owner = self.db.query(User).filter(User.id == owner_user_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="User not found")

        row = MemoryList(
            id=uuid.uuid4().hex,
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_list_item(self, *, owner_user_id: str, list_id: str, text: str) -> MemoryListItem:
        lst = (
            self.db.query(MemoryList)
            .filter(MemoryList.id == list_id)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.deleted_at.is_(None))
            .first()
        )
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")

        # Position = max+1 (cheap, simple)
        last = (
            self.db.query(MemoryListItem)
            .filter(MemoryListItem.list_id == list_id)
            .filter(MemoryListItem.deleted_at.is_(None))
            .order_by(MemoryListItem.position.desc())
            .first()
        )
        pos = int(last.position) + 1 if last else 0

        item = MemoryListItem(
            id=uuid.uuid4().hex,
            list_id=list_id,
            position=pos,
            text=text,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    # -----------------------
    # Reminders (storage only for now; scheduling handled separately)
    # -----------------------

    def list_reminders(
        self,
        *,
        owner_user_id: str,
        status: Optional[ReminderStatus] = None,
        limit: int = 50,
    ) -> List[Reminder]:
        q = (
            self.db.query(Reminder)
            .filter(Reminder.owner_user_id == owner_user_id)
            .order_by(Reminder.due_at.asc())
            .limit(max(1, min(limit, 200)))
        )
        if status is not None:
            q = q.filter(Reminder.status == status)
        return list(q.all())

    def cancel_reminder(self, *, owner_user_id: str, reminder_id: str) -> Reminder:
        r = (
            self.db.query(Reminder)
            .filter(Reminder.id == reminder_id)
            .filter(Reminder.owner_user_id == owner_user_id)
            .first()
        )
        if not r:
            raise HTTPException(status_code=404, detail="Reminder not found")
        if r.status == ReminderStatus.CANCELLED:
            return r
        r.status = ReminderStatus.CANCELLED
        r.cancelled_at = _now()
        r.updated_at = _now()
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        return r

    def list_lists(self, *, owner_user_id: str, limit: int = 50) -> List[MemoryList]:
        q = (
            self.db.query(MemoryList)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.deleted_at.is_(None))
            .order_by(MemoryList.updated_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        return list(q.all())

    def list_list_items(
        self, *, owner_user_id: str, list_id: str, include_completed: bool = True
    ) -> List[MemoryListItem]:
        lst = (
            self.db.query(MemoryList)
            .filter(MemoryList.id == list_id)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.deleted_at.is_(None))
            .first()
        )
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")

        q = (
            self.db.query(MemoryListItem)
            .filter(MemoryListItem.list_id == list_id)
            .filter(MemoryListItem.deleted_at.is_(None))
            .order_by(MemoryListItem.position.asc())
        )
        if not include_completed:
            q = q.filter(MemoryListItem.completed_at.is_(None))
        return list(q.all())

    def complete_list_item(
        self, *, owner_user_id: str, list_id: str, item_id: str
    ) -> MemoryListItem:
        lst = (
            self.db.query(MemoryList)
            .filter(MemoryList.id == list_id)
            .filter(MemoryList.owner_user_id == owner_user_id)
            .filter(MemoryList.deleted_at.is_(None))
            .first()
        )
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")

        item = (
            self.db.query(MemoryListItem)
            .filter(MemoryListItem.id == item_id)
            .filter(MemoryListItem.list_id == list_id)
            .filter(MemoryListItem.deleted_at.is_(None))
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="List item not found")

        item.completed_at = _now()
        item.updated_at = _now()
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def create_reminder(
        self,
        *,
        owner_user_id: str,
        body: str,
        due_at: datetime,
        title: Optional[str] = None,
        timezone_name: Optional[str] = None,
        rrule: Optional[str] = None,
        delivery: Optional[dict] = None,
        source_message_id: Optional[str] = None,
    ) -> Reminder:
        owner = self.db.query(User).filter(User.id == owner_user_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="User not found")

        r = Reminder(
            id=uuid.uuid4().hex,
            owner_user_id=owner_user_id,
            title=title,
            body=body,
            due_at=due_at,
            timezone=timezone_name,
            rrule=rrule,
            delivery=delivery or {},
            source_message_id=source_message_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self.db.add(r)
        self.db.commit()
        self.db.refresh(r)
        return r

    def find_calendar_reminder(
        self,
        *,
        owner_user_id: str,
        google_event_id: str,
    ) -> Optional[Reminder]:
        rows = (
            self.db.query(Reminder)
            .filter(Reminder.owner_user_id == owner_user_id)
            .all()
        )
        for row in rows:
            delivery = row.delivery or {}
            if delivery.get("source") == "google_calendar" and delivery.get("google_event_id") == google_event_id:
                return row
        return None

    def upsert_calendar_reminder(
        self,
        *,
        owner_user_id: str,
        google_event_id: str,
        google_calendar_id: str,
        google_etag: Optional[str],
        event_start: datetime,
        title: str,
        body: str,
        due_at: datetime,
        timezone_name: Optional[str],
        delivery: dict,
    ) -> tuple[Reminder, bool]:
        delivery_payload = {
            **(delivery or {}),
            "source": "google_calendar",
            "google_event_id": google_event_id,
            "google_calendar_id": google_calendar_id,
            "google_etag": google_etag,
            "event_start": event_start.isoformat(),
        }

        existing = self.find_calendar_reminder(
            owner_user_id=owner_user_id,
            google_event_id=google_event_id,
        )
        if existing:
            existing.title = title
            existing.body = body
            existing.due_at = due_at
            existing.timezone = timezone_name
            existing.delivery = delivery_payload
            if existing.status != ReminderStatus.SENT:
                existing.status = ReminderStatus.SCHEDULED
            existing.updated_at = _now()
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing, False

        reminder = self.create_reminder(
            owner_user_id=owner_user_id,
            title=title,
            body=body,
            due_at=due_at,
            timezone_name=timezone_name,
            delivery=delivery_payload,
        )
        return reminder, True

    def cancel_calendar_reminder(
        self,
        *,
        owner_user_id: str,
        google_event_id: str,
    ) -> bool:
        existing = self.find_calendar_reminder(
            owner_user_id=owner_user_id,
            google_event_id=google_event_id,
        )
        if not existing:
            return False
        if existing.status == ReminderStatus.CANCELLED:
            return False
        existing.status = ReminderStatus.CANCELLED
        existing.cancelled_at = _now()
        existing.updated_at = _now()
        self.db.add(existing)
        self.db.commit()
        return True

