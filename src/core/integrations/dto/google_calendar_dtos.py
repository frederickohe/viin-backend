from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class GoogleCalendarConnectResponse(BaseModel):
    authorization_url: str


class GoogleCalendarStatusResponse(BaseModel):
    connected: bool
    google_account_email: Optional[str] = None
    calendar_id: Optional[str] = None
    reminder_lead_minutes: int = 15
    last_synced_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
    enabled: bool = False


class GoogleCalendarSettingsUpdateRequest(BaseModel):
    reminder_lead_minutes: Optional[int] = Field(default=None, ge=0, le=1440)


class GoogleCalendarSyncResponse(BaseModel):
    synced_events: int
    reminders_created: int
    reminders_updated: int
    reminders_cancelled: int


class GoogleCalendarDisconnectResponse(BaseModel):
    message: str
