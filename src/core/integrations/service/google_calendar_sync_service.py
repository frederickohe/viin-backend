from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from config import settings
from core.integrations.model.google_calendar_connection import GoogleCalendarConnection
from core.integrations.service.google_calendar_oauth_service import (
    GOOGLE_CALENDAR_SCOPE,
    GoogleCalendarOAuthService,
)
from core.memory.model.memory_enums import ReminderStatus
from core.memory.service.memory_service import MemoryService
from core.memory.service.reminder_delivery_service import ReminderDeliveryService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SyncStats:
    synced_events: int = 0
    reminders_created: int = 0
    reminders_updated: int = 0
    reminders_cancelled: int = 0


class GoogleCalendarSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.oauth = GoogleCalendarOAuthService(db)
        self.memory = MemoryService(db)

    def sync_all_enabled_connections(self) -> int:
        connections = (
            self.db.query(GoogleCalendarConnection)
            .filter(GoogleCalendarConnection.enabled.is_(True))
            .all()
        )
        synced = 0
        for conn in connections:
            try:
                self.sync_connection(conn)
                synced += 1
            except Exception as exc:
                logger.error(
                    "[GOOGLE_CALENDAR] sync failed connection_id=%s user=%s err=%s",
                    conn.id,
                    conn.user_id,
                    exc,
                    exc_info=True,
                )
                conn.last_sync_error = str(exc)
                conn.updated_at = _now()
                self.db.add(conn)
                self.db.commit()
        return synced

    def sync_connection(self, conn: GoogleCalendarConnection) -> SyncStats:
        access_token = self.oauth.get_valid_access_token(conn)
        credentials = Credentials(
            token=access_token,
            refresh_token=None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            scopes=[GOOGLE_CALENDAR_SCOPE],
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

        now = _now()
        horizon_days = max(1, settings.GOOGLE_CALENDAR_SYNC_HORIZON_DAYS)
        time_min = now
        time_max = now + timedelta(days=horizon_days)

        events: List[dict] = []
        page_token: Optional[str] = None
        while True:
            response = (
                service.events()
                .list(
                    calendarId=conn.calendar_id or "primary",
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    showDeleted=True,
                    maxResults=250,
                    pageToken=page_token,
                )
                .execute()
            )
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        stats = SyncStats(synced_events=len(events))
        lead_minutes = conn.reminder_lead_minutes
        delivery = ReminderDeliveryService.default_delivery_for_owner(conn.user_id)

        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue

            if event.get("status") == "cancelled":
                if self.memory.cancel_calendar_reminder(
                    owner_user_id=conn.user_id,
                    google_event_id=event_id,
                ):
                    stats.reminders_cancelled += 1
                continue

            event_start = self._parse_event_start(event)
            if not event_start:
                continue

            if event_start <= now:
                continue

            due_at = event_start - timedelta(minutes=lead_minutes)
            if due_at <= now:
                due_at = now

            title = (event.get("summary") or "Calendar event").strip()
            body = (event.get("description") or title).strip()
            timezone_name = event.get("start", {}).get("timeZone")

            reminder, created = self.memory.upsert_calendar_reminder(
                owner_user_id=conn.user_id,
                google_event_id=event_id,
                google_calendar_id=conn.calendar_id or "primary",
                google_etag=event.get("etag"),
                event_start=event_start,
                title=title[:200],
                body=body,
                due_at=due_at,
                timezone_name=timezone_name,
                delivery=delivery,
            )
            if created:
                stats.reminders_created += 1
            elif reminder.status == ReminderStatus.SCHEDULED:
                stats.reminders_updated += 1

        conn.last_synced_at = _now()
        conn.last_sync_error = None
        conn.updated_at = _now()
        self.db.add(conn)
        self.db.commit()
        return stats

    @staticmethod
    def _parse_event_start(event: dict) -> Optional[datetime]:
        start = event.get("start") or {}
        raw = start.get("dateTime")
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
