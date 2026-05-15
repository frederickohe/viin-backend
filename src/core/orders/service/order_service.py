"""Order Service"""
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime
import logging
import uuid
import secrets
import string

from core.orders.model.order import (
    Order, OrderType, OrderStatus, OrderPaymentStatus,
    FulfillmentStatus, OrderPaymentMethod, OrderSource
)
from core.orders.dto.order_create_dto import OrderCreateDTO
from core.orders.dto.order_update_dto import OrderUpdateDTO
from core.orders.dto.order_response_dto import OrderResponseDTO
from core.notification.service.event_notification_service import EventNotificationService

logger = logging.getLogger(__name__)

# Order statuses the admin queue should surface (needs fulfillment / follow-up).
_ADMIN_ACTIVE_STATUSES = (
    OrderStatus.PENDING.value,
    OrderStatus.PROCESSING.value,
    OrderStatus.CONFIRMED.value,
)


class OrderService:
    """
    Service for managing customer orders.
    Handles CRUD operations and order lifecycle management.
    """

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _generate_order_number() -> str:
        """Generate a unique order number."""
        timestamp = datetime.utcnow().strftime("%Y%m%d")
        random_suffix = ''.join(secrets.choice(string.digits) for _ in range(5))
        return f"ORD-{timestamp}-{random_suffix}"

    def create_order(self, order_data: OrderCreateDTO) -> Tuple[bool, Optional[Order], str]:
        """
        Create a new order.

        Args:
            order_data: OrderCreateDTO with order details

        Returns:
            Tuple of (success, order_object, message)
        """
        try:
            logger.info(f"[ORDER_SERVICE] Creating order for customer phone: {order_data.customer_phone}")

            # Validate order type
            try:
                OrderType(order_data.order_type)
            except ValueError:
                return False, None, f"Invalid order_type: {order_data.order_type}"

            # Validate optional fields
            if order_data.order_source:
                try:
                    OrderSource(order_data.order_source)
                except ValueError:
                    return False, None, f"Invalid order_source: {order_data.order_source}"

            if order_data.payment_method:
                try:
                    OrderPaymentMethod(order_data.payment_method)
                except ValueError:
                    return False, None, f"Invalid payment_method: {order_data.payment_method}"

            # Calculate total amount (cap discount so unpriced / partial orders stay valid)
            billable_base = (
                order_data.subtotal_amount +
                order_data.tax_amount +
                order_data.shipping_amount
            )
            effective_discount = order_data.discount_amount
            if effective_discount > billable_base:
                logger.warning(
                    "[ORDER_SERVICE] Discount %s exceeds billable base %s; capping discount",
                    effective_discount,
                    billable_base,
                )
                effective_discount = billable_base

            total_amount = billable_base - effective_discount
            total_quantity = int(order_data.quantity)

            if total_amount < 0:
                return False, None, "Total amount cannot be negative"

            # Generate unique order number
            order_number = self._generate_order_number()

            # Create order
            order = Order(
                order_number=order_number,
                customer_id=None,
                customer_name=order_data.customer_name,
                customer_phone=order_data.customer_phone,
                customer_email=order_data.customer_email,
                customer_location=order_data.customer_location,
                order_type=order_data.order_type,
                # Persist as structured JSON internally while exposing simplified DTO fields.
                order_items=[{"name": order_data.item_name, "quantity": int(order_data.quantity)}],
                order_status=OrderStatus.PENDING.value,
                payment_status=OrderPaymentStatus.PENDING.value,
                fulfillment_status=FulfillmentStatus.UNFULFILLED.value,
                subtotal_amount=order_data.subtotal_amount,
                total_quantity=total_quantity,
                discount_amount=effective_discount,
                tax_amount=order_data.tax_amount,
                shipping_amount=order_data.shipping_amount,
                total_amount=total_amount,
                currency_code=order_data.currency_code,
                payment_method=order_data.payment_method,
                payment_reference=order_data.payment_reference,
                payment_details=order_data.payment_details,
                order_source=order_data.order_source,
                notes=order_data.notes,
                tags=order_data.tags,
                custom_metadata=order_data.custom_metadata
            )

            self.db.add(order)
            self.db.commit()
            self.db.refresh(order)

            logger.info(f"[ORDER_SERVICE] Order created successfully: {order.order_number}")

            try:
                EventNotificationService(self.db).notify_order_created(order)
            except Exception as notify_exc:
                logger.error(
                    "[ORDER_SERVICE] Order created but notification failed: %s",
                    notify_exc,
                    exc_info=True,
                )

            return True, order, f"Order {order.order_number} created successfully!"

        except Exception as e:
            self.db.rollback()
            logger.error(f"[ORDER_SERVICE] Error creating order: {str(e)}", exc_info=True)
            return False, None, f"Error creating order: {str(e)}"

    def get_order_by_id(self, order_id: str) -> Optional[Order]:
        """Get an order by its ID."""
        try:
            logger.info(f"[ORDER_SERVICE] Fetching order: {order_id}")
            order = self.db.query(Order).filter(Order.order_id == uuid.UUID(order_id)).first()
            return order
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching order: {str(e)}", exc_info=True)
            return None

    def get_order_by_number(self, order_number: str) -> Optional[Order]:
        """Get an order by its order number."""
        try:
            logger.info(f"[ORDER_SERVICE] Fetching order by number: {order_number}")
            order = self.db.query(Order).filter(Order.order_number == order_number).first()
            return order
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching order: {str(e)}", exc_info=True)
            return None

    def get_customer_orders(self, customer_id: str, skip: int = 0, limit: int = 100) -> List[Order]:
        """Get all orders for a customer."""
        try:
            logger.info(f"[ORDER_SERVICE] Fetching orders for customer: {customer_id}")
            orders = self.db.query(Order).filter(
                Order.customer_id == customer_id
            ).order_by(desc(Order.order_date)).offset(skip).limit(limit).all()
            logger.info(f"[ORDER_SERVICE] Found {len(orders)} orders for customer")
            return orders
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching customer orders: {str(e)}", exc_info=True)
            return []

    def get_all_orders(self, skip: int = 0, limit: int = 100, order_status: Optional[str] = None) -> List[Order]:
        """Get all orders with optional filtering."""
        try:
            query = self.db.query(Order)
            
            if order_status:
                try:
                    OrderStatus(order_status)
                    query = query.filter(Order.order_status == order_status)
                except ValueError:
                    logger.warning(f"[ORDER_SERVICE] Invalid order_status filter: {order_status}")

            orders = query.order_by(desc(Order.order_date)).offset(skip).limit(limit).all()
            logger.info(f"[ORDER_SERVICE] Found {len(orders)} orders")
            return orders
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching orders: {str(e)}", exc_info=True)
            return []

    def get_admin_active_orders(self, skip: int = 0, limit: int = 100) -> List[Order]:
        """Orders that still need admin attention (not completed or cancelled)."""
        try:
            orders = (
                self.db.query(Order)
                .filter(Order.order_status.in_(_ADMIN_ACTIVE_STATUSES))
                .order_by(desc(Order.order_date))
                .offset(skip)
                .limit(limit)
                .all()
            )
            logger.info(f"[ORDER_SERVICE] Found {len(orders)} admin active orders")
            return orders
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching admin active orders: {str(e)}", exc_info=True)
            return []

    def get_admin_completed_orders(self, skip: int = 0, limit: int = 100) -> List[Order]:
        """Orders finished successfully (completed lifecycle)."""
        try:
            orders = (
                self.db.query(Order)
                .filter(Order.order_status == OrderStatus.COMPLETED.value)
                .order_by(desc(Order.order_date))
                .offset(skip)
                .limit(limit)
                .all()
            )
            logger.info(f"[ORDER_SERVICE] Found {len(orders)} admin completed orders")
            return orders
        except Exception as e:
            logger.error(f"[ORDER_SERVICE] Error fetching admin completed orders: {str(e)}", exc_info=True)
            return []

    def update_order(self, order_id: str, update_data: OrderUpdateDTO) -> Tuple[bool, Optional[Order], str]:
        """
        Update an existing order.

        Args:
            order_id: Order ID
            update_data: OrderUpdateDTO with fields to update

        Returns:
            Tuple of (success, order_object, message)
        """
        try:
            logger.info(f"[ORDER_SERVICE] Updating order: {order_id}")

            order = self.get_order_by_id(order_id)
            if not order:
                return False, None, "Order not found"

            # Validate statuses if provided
            if update_data.order_status:
                try:
                    OrderStatus(update_data.order_status)
                except ValueError:
                    return False, None, f"Invalid order_status: {update_data.order_status}"

            if update_data.payment_status:
                try:
                    OrderPaymentStatus(update_data.payment_status)
                except ValueError:
                    return False, None, f"Invalid payment_status: {update_data.payment_status}"

            if update_data.fulfillment_status:
                try:
                    FulfillmentStatus(update_data.fulfillment_status)
                except ValueError:
                    return False, None, f"Invalid fulfillment_status: {update_data.fulfillment_status}"

            if update_data.payment_method:
                try:
                    OrderPaymentMethod(update_data.payment_method)
                except ValueError:
                    return False, None, f"Invalid payment_method: {update_data.payment_method}"

            # Update fields
            if update_data.order_status:
                order.order_status = update_data.order_status

            if update_data.payment_status:
                order.payment_status = update_data.payment_status
                # Set payment_date if status changed to paid
                if update_data.payment_status in [OrderPaymentStatus.PAID.value]:
                    order.payment_date = datetime.utcnow()

            if update_data.fulfillment_status:
                order.fulfillment_status = update_data.fulfillment_status
                # Set fulfillment_date if status changed to fulfilled/shipped
                if update_data.fulfillment_status in [
                    FulfillmentStatus.FULFILLED.value,
                    FulfillmentStatus.SHIPPED.value,
                    FulfillmentStatus.DELIVERED.value
                ]:
                    if update_data.fulfillment_status == FulfillmentStatus.DELIVERED.value:
                        order.delivery_date = datetime.utcnow()
                    else:
                        order.fulfillment_date = datetime.utcnow()

            if update_data.subtotal_amount:
                order.subtotal_amount = update_data.subtotal_amount
            if update_data.quantity is not None:
                order.total_quantity = update_data.quantity

            if update_data.discount_amount is not None:
                order.discount_amount = update_data.discount_amount

            if update_data.tax_amount is not None:
                order.tax_amount = update_data.tax_amount

            if update_data.shipping_amount is not None:
                order.shipping_amount = update_data.shipping_amount

            if update_data.payment_method:
                order.payment_method = update_data.payment_method

            if update_data.payment_reference:
                order.payment_reference = update_data.payment_reference

            if update_data.payment_details:
                order.payment_details = update_data.payment_details
            if update_data.item_name is not None or update_data.quantity is not None:
                current_item = {}
                if isinstance(order.order_items, list) and order.order_items and isinstance(order.order_items[0], dict):
                    current_item = order.order_items[0].copy()
                elif isinstance(order.order_items, dict):
                    current_item = order.order_items.copy()

                if update_data.item_name is not None:
                    current_item["name"] = update_data.item_name
                if update_data.quantity is not None:
                    current_item["quantity"] = int(update_data.quantity)
                    order.total_quantity = int(update_data.quantity)

                order.order_items = [current_item]

            if update_data.customer_email:
                order.customer_email = update_data.customer_email
            if update_data.customer_name:
                order.customer_name = update_data.customer_name
            if update_data.customer_phone:
                order.customer_phone = update_data.customer_phone
            if update_data.customer_location is not None:
                order.customer_location = update_data.customer_location

            if update_data.notes is not None:
                order.notes = update_data.notes

            if update_data.tags is not None:
                order.tags = update_data.tags

            if update_data.custom_metadata is not None:
                order.custom_metadata = update_data.custom_metadata

            # Recalculate total if amounts changed
            if any([
                update_data.subtotal_amount,
                update_data.discount_amount is not None,
                update_data.tax_amount is not None,
                update_data.shipping_amount is not None
            ]):
                order.total_amount = (
                    order.subtotal_amount +
                    order.tax_amount +
                    order.shipping_amount -
                    order.discount_amount
                )

            order.updated_at = datetime.utcnow()

            self.db.commit()
            self.db.refresh(order)

            logger.info(f"[ORDER_SERVICE] Order updated successfully: {order.order_number}")
            return True, order, f"Order {order.order_number} updated successfully!"

        except Exception as e:
            self.db.rollback()
            logger.error(f"[ORDER_SERVICE] Error updating order: {str(e)}", exc_info=True)
            return False, None, f"Error updating order: {str(e)}"

    def cancel_order(self, order_id: str, reason: Optional[str] = None) -> Tuple[bool, Optional[Order], str]:
        """Cancel an order."""
        try:
            logger.info(f"[ORDER_SERVICE] Cancelling order: {order_id}")

            order = self.get_order_by_id(order_id)
            if not order:
                return False, None, "Order not found"

            if order.order_status == OrderStatus.CANCELLED.value:
                return False, None, "Order is already cancelled"

            if order.order_status == OrderStatus.COMPLETED.value:
                return False, None, "Cannot cancel a completed order"

            order.order_status = OrderStatus.CANCELLED.value
            if reason:
                order.notes = f"{order.notes or ''}\nCancellation reason: {reason}".strip()

            order.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(order)

            logger.info(f"[ORDER_SERVICE] Order cancelled successfully: {order.order_number}")
            return True, order, f"Order {order.order_number} cancelled successfully!"

        except Exception as e:
            self.db.rollback()
            logger.error(f"[ORDER_SERVICE] Error cancelling order: {str(e)}", exc_info=True)
            return False, None, f"Error cancelling order: {str(e)}"

    def complete_order(self, order_id: str) -> Tuple[bool, Optional[Order], str]:
        """Mark an order as completed."""
        try:
            logger.info(f"[ORDER_SERVICE] Completing order: {order_id}")

            order = self.get_order_by_id(order_id)
            if not order:
                return False, None, "Order not found"

            if order.order_status == OrderStatus.CANCELLED.value:
                return False, None, "Cannot complete a cancelled order"

            if order.order_status == OrderStatus.COMPLETED.value:
                return False, None, "Order is already completed"

            order.order_status = OrderStatus.COMPLETED.value
            order.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(order)

            logger.info(f"[ORDER_SERVICE] Order completed successfully: {order.order_number}")
            return True, order, f"Order {order.order_number} marked as completed!"

        except Exception as e:
            self.db.rollback()
            logger.error(f"[ORDER_SERVICE] Error completing order: {str(e)}", exc_info=True)
            return False, None, f"Error completing order: {str(e)}"

    def delete_order(self, order_id: str) -> Tuple[bool, str]:
        """Delete an order (hard delete)."""
        try:
            logger.info(f"[ORDER_SERVICE] Deleting order: {order_id}")

            order = self.get_order_by_id(order_id)
            if not order:
                return False, "Order not found"

            self.db.delete(order)
            self.db.commit()

            logger.info(f"[ORDER_SERVICE] Order deleted successfully: {order_id}")
            return True, "Order deleted successfully!"

        except Exception as e:
            self.db.rollback()
            logger.error(f"[ORDER_SERVICE] Error deleting order: {str(e)}", exc_info=True)
            return False, f"Error deleting order: {str(e)}"

