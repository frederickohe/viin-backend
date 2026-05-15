"""In-app notifications for operational events (interventions, orders)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from config import settings
from core.notification.model.Notification import NotificationType
from core.notification.service.notification_service import NotificationService
from core.orders.model.order import Order
from core.user.model.User import User

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

    def notify_intervention_active(
        self,
        *,
        user_id: str,
        intervention_id: int,
        trigger: str,
        reason: Optional[str] = None,
        conversation_date: Optional[str] = None,
    ) -> None:
        """Notify admins that a conversation needs human attention."""
        display_reason = (reason or trigger or "Agent handover").strip()
        data = {
            "event": "intervention_active",
            "title": "Conversation needs attention",
            "content": f"A customer conversation was flagged for human support ({trigger}).",
            "message": f"Intervention #{intervention_id}: {display_reason}",
            "user_id": user_id,
            "intervention_id": intervention_id,
            "trigger": trigger,
            "reason": reason,
            "conversation_date": conversation_date,
        }
        self._notify_admins(NotificationType.ALERT, data)

    def notify_order_created(self, order: Order) -> None:
        """Notify admins (and the customer if registered) about a new order."""
        item_name = None
        quantity = None
        if order.order_items and isinstance(order.order_items, list) and order.order_items:
            first_item = order.order_items[0] or {}
            if isinstance(first_item, dict):
                item_name = first_item.get("name")
                quantity = first_item.get("quantity")

        admin_data = {
            "event": "order_created",
            "title": "New order received",
            "content": (
                f"Order {order.order_number} from {order.customer_name or 'a customer'} "
                f"({order.customer_phone or 'no phone'})."
            ),
            "message": (
                f"New order {order.order_number}: "
                f"{item_name or 'Item'} x{quantity or order.total_quantity} — "
                f"{order.total_amount} {order.currency_code}"
            ),
            "order_id": str(order.order_id),
            "order_number": order.order_number,
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "item_name": item_name,
            "quantity": quantity,
            "total_amount": str(order.total_amount),
            "currency_code": order.currency_code,
            "order_status": order.order_status,
        }
        self._notify_admins(NotificationType.TRANSACTIONAL, admin_data)

        if order.customer_phone:
            customer_user_id = self._resolve_user_db_id(order.customer_phone)
            if customer_user_id:
                customer_data = {
                    "event": "order_created",
                    "title": "Order placed",
                    "content": f"Your order {order.order_number} has been received.",
                    "message": (
                        f"Order {order.order_number} confirmed. "
                        f"Total: {order.total_amount} {order.currency_code}"
                    ),
                    "order_id": str(order.order_id),
                    "order_number": order.order_number,
                }
                self._notify_user_safe(
                    customer_user_id,
                    NotificationType.SUCCESS,
                    customer_data,
                )
