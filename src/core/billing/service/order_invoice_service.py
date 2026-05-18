"""Create Paystack invoices for orders and deliver them to customer conversations."""

from __future__ import annotations

import logging
import os
import re
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.billing.dto.request.billing_create import BillingCreateRequest
from core.billing.model.billing_charge import BillingSourceType
from core.billing.service.billing_service import BillingService
from core.nlu.model.Conversation import DailyConversation
from core.nlu.service.conversation_manager import ConversationManager
from core.orders.dto.order_update_dto import OrderUpdateDTO
from core.orders.model.order import Order
from core.orders.service.order_service import OrderService
from utilities.phone_utils import normalize_ghana_phone_number

logger = logging.getLogger(__name__)


class OrderInvoiceService:
    def __init__(self, db: Session):
        self.db = db
        self.order_service = OrderService(db)
        self.billing_service = BillingService(db)

    def send_invoice_for_order(
        self,
        *,
        merchant_user_id: str,
        order_id: Optional[str] = None,
        order_number: Optional[str] = None,
        customer_email: Optional[str] = None,
        created_by_user_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        order = self._resolve_order(order_id=order_id, order_number=order_number)
        if not order:
            return False, "❌ Order not found. Provide a valid order number or order ID."

        if not self._merchant_owns_order(order, merchant_user_id):
            return False, "❌ You do not have permission to invoice this order."

        email = (customer_email or order.customer_email or "").strip()
        if not email:
            email = self._email_from_phone(order.customer_phone)
        if not email or "@" not in email:
            return (
                False,
                "❌ Customer email is required for Paystack invoicing. "
                "Add customer_email on the order or include it in your request.",
            )

        amount = Decimal(str(order.total_amount or 0))
        if amount <= 0:
            return False, "❌ Order total must be greater than zero before sending an invoice."

        item_summary = self._order_item_summary(order)
        description = f"Invoice for order {order.order_number}"
        if item_summary:
            description = f"{description} — {item_summary}"

        billing_request = BillingCreateRequest(
            customer_email=email,
            customer_name=order.customer_name,
            amount=amount,
            currency=(order.currency_code or "GHS").upper(),
            description=description,
            external_id=str(order.order_id),
            source_type=BillingSourceType.ORDER,
            metadata={
                "order_id": str(order.order_id),
                "order_number": order.order_number,
                "customer_phone": order.customer_phone,
            },
        )

        try:
            billing = self.billing_service.create_billing_sync(
                billing_request,
                created_by_user_id=created_by_user_id,
            )
        except Exception as exc:
            logger.error("[ORDER_INVOICE] Paystack billing failed: %s", exc, exc_info=True)
            return False, f"❌ Could not create payment link: {str(exc)[:200]}"

        payment_url = billing.payment_url or ""
        self._update_order_payment_fields(order, billing.reference, payment_url)

        customer_message = self._format_customer_invoice_message(order, billing.reference, payment_url)
        conversation_user_id = self._post_to_customer_conversation(order, customer_message)
        whatsapp_sent = self._try_whatsapp_invoice(order, customer_message)

        merchant_lines = [
            f"✅ Invoice sent for order {order.order_number}.",
            f"Amount: {order.total_amount} {order.currency_code}",
            f"Paystack reference: {billing.reference}",
        ]
        if payment_url:
            merchant_lines.append(f"Payment link: {payment_url}")
        if conversation_user_id:
            merchant_lines.append(f"Posted to customer chat ({conversation_user_id}).")
        elif order.customer_phone:
            merchant_lines.append(
                "No active chat session found for this customer; link was still generated."
            )
        if whatsapp_sent:
            merchant_lines.append("Also sent via WhatsApp.")

        return True, "\n".join(merchant_lines)

    def _resolve_order(
        self, *, order_id: Optional[str], order_number: Optional[str]
    ) -> Optional[Order]:
        if order_number:
            order = self.order_service.get_order_by_number(str(order_number).strip())
            if order:
                return order
        if order_id:
            return self.order_service.get_order_by_id(str(order_id).strip())
        return None

    def _merchant_owns_order(self, order: Order, merchant_user_id: str) -> bool:
        if not order.user_id:
            return True
        resolved = self.order_service._resolve_user_db_id(merchant_user_id)
        if not resolved:
            return False
        return str(order.user_id) == str(resolved)

    @staticmethod
    def _email_from_phone(phone: Optional[str]) -> Optional[str]:
        if not phone:
            return None
        digits = re.sub(r"\D", "", phone)
        if not digits:
            return None
        return f"pay.{digits}@billing.autobus.app"

    @staticmethod
    def _order_item_summary(order: Order) -> str:
        if not order.order_items or not isinstance(order.order_items, list):
            return ""
        first = order.order_items[0] if order.order_items else {}
        if not isinstance(first, dict):
            return ""
        name = first.get("name") or "Item"
        qty = first.get("quantity")
        return f"{name} x{qty}" if qty else str(name)

    def _update_order_payment_fields(
        self, order: Order, reference: str, payment_url: str
    ) -> None:
        update = OrderUpdateDTO(
            payment_reference=reference,
            payment_details={
                "provider": "paystack",
                "reference": reference,
                "payment_url": payment_url,
            },
        )
        self.order_service.update_order(str(order.order_id), update)

    @staticmethod
    def _format_customer_invoice_message(
        order: Order, reference: str, payment_url: str
    ) -> str:
        lines = [
            f"🧾 Invoice for order {order.order_number}",
            f"Amount due: {order.total_amount} {order.currency_code}",
        ]
        item_summary = OrderInvoiceService._order_item_summary(order)
        if item_summary:
            lines.append(f"Items: {item_summary}")
        if payment_url:
            lines.append(f"\nPay securely here:\n{payment_url}")
        lines.append(f"\nReference: {reference}")
        return "\n".join(lines)

    def _conversation_user_candidates(self, customer_phone: Optional[str]) -> set[str]:
        candidates: set[str] = set()
        if not customer_phone:
            return candidates

        raw = str(customer_phone).strip()
        candidates.add(raw)

        intl = normalize_ghana_phone_number(raw)
        if intl:
            candidates.add(intl)
            if intl.startswith("233") and len(intl) > 3:
                candidates.add("0" + intl[3:])

        local = "".join(ch for ch in raw if ch.isdigit())
        if local.startswith("233") and len(local) > 3:
            candidates.add("0" + local[3:])
        elif local and not local.startswith("0") and len(local) == 9:
            candidates.add("0" + local)

        return {c for c in candidates if c}

    def _find_active_conversation_user_id(self, customer_phone: Optional[str]) -> Optional[str]:
        candidates = self._conversation_user_candidates(customer_phone)
        for user_id in candidates:
            row = (
                self.db.query(DailyConversation)
                .filter(DailyConversation.user_id == user_id)
                .order_by(desc(DailyConversation.updated_at))
                .first()
            )
            if row:
                state = row.conversation_state or {}
                if state.get("conversation_lifecycle", "active") != "completed":
                    return user_id
        return next(iter(candidates), None) if candidates else None

    def _post_to_customer_conversation(
        self, order: Order, message: str
    ) -> Optional[str]:
        conversation_user_id = self._find_active_conversation_user_id(order.customer_phone)
        if not conversation_user_id:
            return None

        try:
            manager = ConversationManager()
            manager.update_conversation_history(conversation_user_id, "assistant", message)
            logger.info(
                "[ORDER_INVOICE] Posted invoice to conversation user_id=%s order=%s",
                conversation_user_id,
                order.order_number,
            )
            return conversation_user_id
        except Exception as exc:
            logger.error(
                "[ORDER_INVOICE] Failed to post to conversation: %s", exc, exc_info=True
            )
            return None

    def _try_whatsapp_invoice(self, order: Order, message: str) -> bool:
        phone_id = (os.getenv("WHATSAPP_phone_ID") or "").strip()
        if not phone_id or not order.customer_phone:
            return False

        try:
            from core.webhooks.service.whatsapp_service import WhatsAppService

            recipient = normalize_ghana_phone_number(order.customer_phone)
            if not recipient:
                return False

            sent = WhatsAppService().send_message(
                phone_id=phone_id,
                recipient_phone=recipient,
                message_text=message,
                preview_url=True,
            )
            return bool(sent)
        except Exception as exc:
            logger.warning("[ORDER_INVOICE] WhatsApp delivery failed: %s", exc)
            return False
