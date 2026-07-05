"""In-app notifications for operational events."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from config import settings
from core.notification.model.Notification import NotificationType
from core.notification.service.notification_service import NotificationService
from core.user.model.User import User
from core.user.notification_preferences import allows_in_app_notifications, allows_sms_notifications

logger = logging.getLogger(__name__)


class EventNotificationService:
    def __init__(self, db: Session):
        self.db = db
        self._notifications = NotificationService(db)

    def _admin_recipient_ids(self) -> List[str]:
        raw = getattr(settings, "ADMIN_NOTIFICATION_USER_IDS", "") or ""
        return [uid.strip() for uid in raw.split(",") if uid.strip()]

    def _resolve_user_db_id(self, user_identifier: str) -> Optional[str]:
        if not user_identifier:
            return None

        user = self.db.query(User).filter(User.id == user_identifier).first()
        if user:
            return user.id

        user = self.db.query(User).filter(User.email == user_identifier).first()
        if user:
            return user.id

        phone_candidates = {user_identifier}
        normalized = self._normalize_phone_like(user_identifier)
        if normalized:
            phone_candidates.add(normalized)

        user = self.db.query(User).filter(User.phone.in_(list(phone_candidates))).first()
        return user.id if user else None

    @staticmethod
    def _normalize_phone_like(value: str) -> str:
        cleaned = "".join(ch for ch in (value or "") if ch.isdigit())
        if cleaned.startswith("233") and len(cleaned) > 3:
            cleaned = "0" + cleaned[3:]
        elif cleaned and not cleaned.startswith("0") and len(cleaned) == 9:
            cleaned = "0" + cleaned
        return cleaned

    def _notify_user_safe(
        self,
        user_id: str,
        notification_type: NotificationType,
        data: Dict[str, Any],
        *,
        send_sms: bool = False,
    ) -> None:
        user = self.db.query(User).filter(User.id == user_id).first()
        if user and not allows_in_app_notifications(user):
            logger.debug(
                "[EVENT_NOTIFICATION] Skipping in-app notify for user %s (%s): disabled",
                user_id,
                data.get("event"),
            )
            return
        if send_sms and user and not allows_sms_notifications(user):
            send_sms = False

        try:
            self._notifications.create_notification(
                user_id=user_id,
                notification_type=notification_type,
                data=data,
                send_sms=send_sms,
            )
        except Exception as exc:
            logger.error(
                "[EVENT_NOTIFICATION] Failed to notify user %s (%s): %s",
                user_id,
                data.get("event"),
                exc,
                exc_info=True,
            )

    def _notify_admins(
        self,
        notification_type: NotificationType,
        data: Dict[str, Any],
        *,
        send_sms: bool = False,
    ) -> None:
        recipients = self._admin_recipient_ids()
        if not recipients:
            logger.debug(
                "[EVENT_NOTIFICATION] No ADMIN_NOTIFICATION_USER_IDS configured; skipping admin notify for %s",
                data.get("event"),
            )
            return

        for admin_id in recipients:
            self._notify_user_safe(admin_id, notification_type, data, send_sms=send_sms)
