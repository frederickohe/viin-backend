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
