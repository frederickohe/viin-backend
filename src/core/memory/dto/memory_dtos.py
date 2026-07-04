from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.memory.model.memory_enums import MemoryItemType, MemoryVisibility


class MemoryItemCreateRequest(BaseModel):
    item_type: MemoryItemType
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    file_id: Optional[str] = None
    tags: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    visibility: MemoryVisibility = MemoryVisibility.PRIVATE


class MemoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    owner_user_id: str
    visibility: MemoryVisibility
    item_type: MemoryItemType
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    file_id: Optional[str] = None
    tags: Dict[str, Any]
    # ORM attr is item_metadata (SQLAlchemy reserves "metadata"); API field is metadata.
    metadata: Dict[str, Any] = Field(validation_alias="item_metadata")
    created_at: datetime
    updated_at: datetime


class MemorySearchResponse(BaseModel):
    hits: List[Dict[str, Any]]
    items: List[MemoryItemResponse]


class MemoryListCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class MemoryListResponse(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MemoryListItemCreateRequest(BaseModel):
    text: str = Field(..., min_length=1)


class MemoryListItemResponse(BaseModel):
    id: str
    list_id: str
    position: int
    text: str
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReminderCreateRequest(BaseModel):
    body: str = Field(..., min_length=1)
    due_at: datetime
    title: Optional[str] = None
    timezone: Optional[str] = None
    rrule: Optional[str] = None
    delivery: Dict[str, Any] = Field(default_factory=dict)


class BriefingResponse(BaseModel):
    period: str
    body: str
    item_count: int


class ReminderResponse(BaseModel):
    id: str
    owner_user_id: str
    title: Optional[str] = None
    body: str
    due_at: datetime
    timezone: Optional[str] = None
    rrule: Optional[str] = None
    status: str
    delivery: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

