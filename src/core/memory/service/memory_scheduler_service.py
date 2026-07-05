from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from core.memory.model.delivery_log import MemoryDeliveryLog
from core.memory.model.memory_enums import DeliveryStatus, ReminderStatus
from core.memory.model.reminder import Reminder
from core.memory.service.briefing_service import BriefingPeriod, BriefingService
from core.memory.service.reminder_delivery_service import ReminderDeliveryService
from core.user.model.User import User
from core.user.notification_preferences import allows_in_app_notifications
from core.webhooks.service.whatsapp_service import WhatsAppService
from utilities.dbconfig import SessionLocal

logger = logging.getLogger(__name__)

_REMINDER_DELIVERY_LOCK_PREFIX = "viin:reminder:deliver:"
_REMINDER_DELIVERY_LOCK_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemorySchedulerService:
    _scheduler_instance: Optional[BackgroundScheduler] = None

    DEFAULT_REMINDER_POLL_SECONDS = 20
    DEFAULT_GOOGLE_CALENDAR_SYNC_SECONDS = 900

    def __init__(self) -> None:
        if MemorySchedulerService._scheduler_instance is None:
            MemorySchedulerService._scheduler_instance = BackgroundScheduler()
        self.scheduler = MemorySchedulerService._scheduler_instance
        self.poll_seconds = self._get_poll_interval()
        self.delivery_service = ReminderDeliveryService()

    @staticmethod
    def _get_poll_interval() -> int:
        try:
            return int(os.getenv("MEMORY_REMINDER_POLL_SECONDS", MemorySchedulerService.DEFAULT_REMINDER_POLL_SECONDS))
        except Exception:
            return MemorySchedulerService.DEFAULT_REMINDER_POLL_SECONDS

    @staticmethod
    def _google_calendar_sync_enabled() -> bool:
        from core.integrations.service.google_calendar_oauth_service import GoogleCalendarOAuthService

        return GoogleCalendarOAuthService.is_configured()

    @staticmethod
    def _get_google_calendar_sync_interval() -> int:
        try:
            return int(
                os.getenv(
                    "GOOGLE_CALENDAR_SYNC_SECONDS",
                    MemorySchedulerService.DEFAULT_GOOGLE_CALENDAR_SYNC_SECONDS,
                )
            )
        except Exception:
            return MemorySchedulerService.DEFAULT_GOOGLE_CALENDAR_SYNC_SECONDS

    @staticmethod
    def _daily_briefing_enabled() -> bool:
        return os.getenv("MEMORY_DAILY_BRIEFING_ENABLED", "false").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    @staticmethod
    def _daily_briefing_hour_utc() -> int:
        try:
            hour = int(os.getenv("MEMORY_DAILY_BRIEFING_HOUR_UTC", "8"))
            return max(0, min(23, hour))
        except Exception:
            return 8

    def start(self) -> None:
        """
        Start background polling for due reminders and optional daily briefings.
        This uses a singleton APScheduler instance, similar to PaymentCheckService.
        """
        try:
            job_id = "memory_due_reminders_poll"
            self.scheduler.add_job(
                func=self._poll_due_reminders,
                trigger="interval",
                seconds=max(5, self.poll_seconds),
                id=job_id,
                replace_existing=True,
                max_instances=1,
            )
            if self._daily_briefing_enabled():
                self.scheduler.add_job(
                    func=self._poll_daily_briefings,
                    trigger="interval",
                    seconds=3600,
                    id="memory_daily_briefing_poll",
                    replace_existing=True,
                    max_instances=1,
                )
                logger.info(
                    "[MEMORY_SCHEDULER] Daily briefing enabled (hour_utc=%s)",
                    self._daily_briefing_hour_utc(),
                )
            if self._google_calendar_sync_enabled():
                sync_seconds = max(60, self._get_google_calendar_sync_interval())
                self.scheduler.add_job(
                    func=self._poll_google_calendar_sync,
                    trigger="interval",
                    seconds=sync_seconds,
                    id="google_calendar_sync_poll",
                    replace_existing=True,
                    max_instances=1,
                )
                logger.info(
                    "[MEMORY_SCHEDULER] Google Calendar sync enabled (poll=%ss)",
                    sync_seconds,
                )
            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("[MEMORY_SCHEDULER] Started (poll=%ss)", self.poll_seconds)
        except Exception as e:
            logger.error("[MEMORY_SCHEDULER] Failed to start: %s", e, exc_info=True)

    @staticmethod
    def shutdown() -> None:
        if MemorySchedulerService._scheduler_instance and MemorySchedulerService._scheduler_instance.running:
            MemorySchedulerService._scheduler_instance.shutdown()
            logger.info("[MEMORY_SCHEDULER] Shutdown complete")

    def _poll_due_reminders(self) -> None:
        db = SessionLocal()
        try:
            now = _now()
            due = (
                db.query(Reminder)
                .filter(Reminder.status == ReminderStatus.SCHEDULED)
                .filter(Reminder.due_at <= now)
                .order_by(Reminder.due_at.asc())
                .limit(50)
                .all()
            )
            if not due:
                return

            for r in due:
                try:
                    self._deliver_reminder(db, r)
                except Exception as e:
                    logger.error("[MEMORY_REMINDER] delivery failed reminder_id=%s err=%s", r.id, e, exc_info=True)
        finally:
            db.close()

    def _poll_google_calendar_sync(self) -> None:
        db = SessionLocal()
        try:
            from core.integrations.service.google_calendar_sync_service import GoogleCalendarSyncService

            synced = GoogleCalendarSyncService(db).sync_all_enabled_connections()
            if synced:
                logger.info("[GOOGLE_CALENDAR] Synced %s connection(s)", synced)
        except Exception as e:
            logger.error("[GOOGLE_CALENDAR] sync poll failed: %s", e, exc_info=True)
        finally:
            db.close()

    @staticmethod
    def _acquire_reminder_delivery_lock(reminder_id: str) -> bool:
        """Claim reminder delivery so only one worker sends notifications."""
        try:
            client = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=0,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            key = f"{_REMINDER_DELIVERY_LOCK_PREFIX}{reminder_id}"
            return bool(
                client.set(key, "1", nx=True, ex=_REMINDER_DELIVERY_LOCK_SECONDS)
            )
        except Exception as exc:
            logger.warning(
                "[MEMORY_REMINDER] Redis delivery lock unavailable reminder_id=%s err=%s",
                reminder_id,
                exc,
            )
            return True

    def _deliver_reminder(self, db: Session, r: Reminder) -> None:
        if not self._acquire_reminder_delivery_lock(r.id):
            logger.debug("[MEMORY_REMINDER] skipping duplicate delivery reminder_id=%s", r.id)
            return

        owner_id = (r.owner_user_id or "").strip()
        user = db.query(User).filter(User.id == owner_id).first()

        message = self.delivery_service.build_message(r)
        channels = self.delivery_service.effective_channels(r, user)
        log_user_id = user.id if user else owner_id

        log = MemoryDeliveryLog(
            id=uuid.uuid4().hex,
            user_id=log_user_id,
            channel=",".join(channels),
            kind="reminder",
            reminder_id=r.id,
            subject=r.title,
            body=message,
            payload={
                "due_at": r.due_at.isoformat(),
                "rrule": r.rrule,
                "delivery": r.delivery or {},
                "channels": channels,
            },
            status=DeliveryStatus.PENDING,
            created_at=_now(),
        )
        db.add(log)
        db.commit()

        any_success, channel_results = self.delivery_service.deliver(db, r, user)
        errors = [f"{item['channel']}: {item['error']}" for item in channel_results if item.get("error")]
        log.payload = {**(log.payload or {}), "channel_results": channel_results}

        if any_success:
            log.status = DeliveryStatus.SENT
            log.sent_at = _now()
            next_due = self.delivery_service.advance_recurrence(r)
            if next_due:
                r.due_at = next_due
                r.status = ReminderStatus.SCHEDULED
            else:
                r.status = ReminderStatus.SENT
        else:
            log.status = DeliveryStatus.FAILED
            log.error = "; ".join(errors) if errors else "Delivery failed"
            if not owner_id.startswith("tg:"):
                r.status = ReminderStatus.FAILED

        db.add(log)
        db.add(r)
        db.commit()

    def _poll_daily_briefings(self) -> None:
        now = _now()
        if now.hour != self._daily_briefing_hour_utc():
            return

        db = SessionLocal()
        try:
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            user_ids = self._users_with_pending_work(db)
            for user_id in user_ids:
                if self._briefing_already_sent_today(db, user_id, start_of_day):
                    continue
                try:
                    self._deliver_daily_briefing(db, user_id)
                except Exception as e:
                    logger.error(
                        "[MEMORY_BRIEFING] delivery failed user_id=%s err=%s",
                        user_id,
                        e,
                        exc_info=True,
                    )
        finally:
            db.close()

    @staticmethod
    def _users_with_pending_work(db: Session) -> list[str]:
        reminder_owners = (
            db.query(Reminder.owner_user_id)
            .filter(Reminder.status == ReminderStatus.SCHEDULED)
            .distinct()
            .all()
        )
        from core.memory.model.memory_list import MemoryList, MemoryListItem

        todo_owners = (
            db.query(MemoryList.owner_user_id)
            .join(MemoryListItem, MemoryListItem.list_id == MemoryList.id)
            .filter(MemoryList.deleted_at.is_(None))
            .filter(MemoryListItem.deleted_at.is_(None))
            .filter(MemoryListItem.completed_at.is_(None))
            .distinct()
            .all()
        )
        ids = {row[0] for row in reminder_owners + todo_owners if row and row[0]}
        return list(ids)

    @staticmethod
    def _briefing_already_sent_today(db: Session, user_id: str, start_of_day: datetime) -> bool:
        existing = (
            db.query(MemoryDeliveryLog)
            .filter(MemoryDeliveryLog.user_id == user_id)
            .filter(MemoryDeliveryLog.kind == "daily_briefing")
            .filter(MemoryDeliveryLog.status == DeliveryStatus.SENT)
            .filter(MemoryDeliveryLog.sent_at >= start_of_day)
            .first()
        )
        return existing is not None

    def _deliver_daily_briefing(self, db: Session, user_id: str) -> None:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return
        if not allows_in_app_notifications(user):
            logger.debug(
                "[MEMORY_BRIEFING] Skipping daily briefing for user_id=%s: in-app notifications disabled",
                user_id,
            )
            return

        briefing_svc = BriefingService(db)
        tasks = briefing_svc.collect_tasks(owner_user_id=user_id, period=BriefingPeriod.DAILY)
        if not tasks:
            return

        body = briefing_svc.format_briefing(tasks=tasks, period=BriefingPeriod.DAILY)
        channel = "whatsapp"

        log = MemoryDeliveryLog(
            id=uuid.uuid4().hex,
            user_id=user.id,
            channel=channel,
            kind="daily_briefing",
            reminder_id=None,
            subject="Daily briefing",
            body=body,
            payload={"period": "daily", "item_count": len(tasks)},
            status=DeliveryStatus.PENDING,
            created_at=_now(),
        )
        db.add(log)
        db.commit()

        ok = False
        err: Optional[str] = None

        if channel == "whatsapp":
            phone_id = (os.getenv("WHATSAPP_phone_ID") or "").strip()
            recipient = (getattr(user, "whatsapp_number", None) or getattr(user, "phone", None) or "").strip()
            if not phone_id or not recipient:
                err = "Missing WHATSAPP_phone_ID or recipient phone"
            else:
                ok = WhatsAppService().send_message(
                    phone_id=phone_id, recipient_phone=recipient, message_text=body
                )
                if not ok:
                    err = "WhatsApp send_message failed"
        else:
            err = f"Unsupported channel: {channel}"

        if ok:
            log.status = DeliveryStatus.SENT
            log.sent_at = _now()
        else:
            log.status = DeliveryStatus.FAILED
            log.error = err or "Delivery failed"

        db.add(log)
        db.commit()

