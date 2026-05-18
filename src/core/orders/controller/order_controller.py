"""Order Controller"""
from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import logging

from core.orders.service.order_service import OrderService
from core.billing.service.order_invoice_service import OrderInvoiceService
from core.user.service.user_service import UserService
from core.orders.dto.order_response_dto import OrderResponseDTO
from core.orders.dto.order_create_dto import OrderCreateDTO
from core.orders.dto.order_update_dto import OrderUpdateDTO
from core.user.controller.usercontroller import validate_token, get_db
from another_fastapi_jwt_auth import AuthJWT

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

order_routes = APIRouter()


@order_routes.post("/create", response_model=OrderResponseDTO)
def create_order(
    request: OrderCreateDTO,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Create a new order."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Creating order for customer phone: {request.customer_phone}")

        owner_id = authjwt.get_jwt_subject()
        order_service = OrderService(db)
        success, order, message = order_service.create_order(request, user_id=owner_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[ORDER_CONTROLLER] Order created successfully: {order.order_number}")
        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error creating order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating order: {str(e)}"
        )


@order_routes.get("/admin/active", response_model=List[OrderResponseDTO])
def list_admin_active_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Orders that need admin attention (pending, processing, confirmed)."""
    try:
        logger.info("[ORDER_CONTROLLER] Listing admin active orders")
        order_service = OrderService(db)
        orders = order_service.get_admin_active_orders(skip, limit)
        logger.info(f"[ORDER_CONTROLLER] Found {len(orders)} admin active orders")
        return [OrderResponseDTO.from_order(o) for o in orders]
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error listing admin active orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving active orders: {str(e)}"
        )


@order_routes.get("/admin/completed", response_model=List[OrderResponseDTO])
def list_admin_completed_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Orders successfully completed."""
    try:
        logger.info("[ORDER_CONTROLLER] Listing admin completed orders")
        order_service = OrderService(db)
        orders = order_service.get_admin_completed_orders(skip, limit)
        logger.info(f"[ORDER_CONTROLLER] Found {len(orders)} admin completed orders")
        return [OrderResponseDTO.from_order(o) for o in orders]
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error listing admin completed orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving completed orders: {str(e)}"
        )


@order_routes.get("/me", response_model=List[OrderResponseDTO])
def list_my_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    order_status: str = Query(None, description="Filter by order status"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """Get all orders for the authenticated user (merchant)."""
    try:
        user_id = authjwt.get_jwt_subject()
        logger.info(f"[ORDER_CONTROLLER] Listing orders for current user: {user_id}")

        order_service = OrderService(db)
        orders = order_service.get_orders_by_user(user_id, skip, limit, order_status)

        logger.info(f"[ORDER_CONTROLLER] Found {len(orders)} orders for current user")
        return [OrderResponseDTO.from_order(o) for o in orders]

    except Exception as e:
        logger.error(
            f"[ORDER_CONTROLLER] Error listing current user orders: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving your orders: {str(e)}",
        )


@order_routes.get("/{order_id}", response_model=OrderResponseDTO)
def get_order(
    order_id: str = Path(..., description="Order ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get a specific order by ID."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Getting order: {order_id}")

        order_service = OrderService(db)
        order = order_service.get_order_by_id(order_id)

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error getting order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving order: {str(e)}"
        )


@order_routes.post("/{order_id}/send-invoice")
def send_order_invoice(
    order_id: str = Path(..., description="Order ID"),
    customer_email: Optional[str] = Query(None, description="Override customer email for Paystack"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """Generate a Paystack payment link for the order and send it to the customer chat."""
    try:
        user_service = UserService(db)
        user = user_service.get_current_user(authjwt.get_jwt_subject())
        invoice_service = OrderInvoiceService(db)
        success, message = invoice_service.send_invoice_for_order(
            merchant_user_id=user.id,
            order_id=order_id,
            customer_email=customer_email,
            created_by_user_id=user.id,
        )
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
        return {"success": True, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error sending invoice: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error sending invoice: {str(e)}",
        )


@order_routes.get("/number/{order_number}", response_model=OrderResponseDTO)
def get_order_by_number(
    order_number: str = Path(..., description="Order Number"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get a specific order by order number."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Getting order by number: {order_number}")

        order_service = OrderService(db)
        order = order_service.get_order_by_number(order_number)

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error getting order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving order: {str(e)}"
        )


@order_routes.get("/customer/{customer_id}", response_model=List[OrderResponseDTO])
def get_customer_orders(
    customer_id: str = Path(..., description="Customer ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all orders for a specific customer."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Getting orders for customer: {customer_id}")

        order_service = OrderService(db)
        orders = order_service.get_customer_orders(customer_id, skip, limit)

        logger.info(f"[ORDER_CONTROLLER] Found {len(orders)} orders for customer")
        return [OrderResponseDTO.from_order(o) for o in orders]

    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error getting customer orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving customer orders: {str(e)}"
        )


@order_routes.get("/", response_model=List[OrderResponseDTO])
def list_all_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    order_status: str = Query(None, description="Filter by order status"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all orders with optional filtering."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Listing all orders")

        order_service = OrderService(db)
        orders = order_service.get_all_orders(skip, limit, order_status)

        logger.info(f"[ORDER_CONTROLLER] Found {len(orders)} orders")
        return [OrderResponseDTO.from_order(o) for o in orders]

    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error listing orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving orders: {str(e)}"
        )


@order_routes.put("/{order_id}", response_model=OrderResponseDTO)
def update_order(
    order_id: str = Path(..., description="Order ID"),
    request: OrderUpdateDTO = None,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Update an existing order."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Updating order: {order_id}")

        if not request:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No update data provided"
            )

        order_service = OrderService(db)
        success, order, message = order_service.update_order(order_id, request)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[ORDER_CONTROLLER] Order updated successfully: {order.order_number}")
        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error updating order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating order: {str(e)}"
        )


@order_routes.post("/{order_id}/cancel", response_model=OrderResponseDTO)
def cancel_order(
    order_id: str = Path(..., description="Order ID"),
    reason: str = Query(None, description="Cancellation reason"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Cancel an order."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Cancelling order: {order_id}")

        order_service = OrderService(db)
        success, order, message = order_service.cancel_order(order_id, reason)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[ORDER_CONTROLLER] Order cancelled successfully: {order.order_number}")
        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error cancelling order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error cancelling order: {str(e)}"
        )


@order_routes.post("/{order_id}/complete", response_model=OrderResponseDTO)
def complete_order(
    order_id: str = Path(..., description="Order ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Mark an order as completed (admin close-out)."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Completing order: {order_id}")

        order_service = OrderService(db)
        success, order, message = order_service.complete_order(order_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[ORDER_CONTROLLER] Order completed successfully: {order.order_number}")
        return OrderResponseDTO.from_order(order)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error completing order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error completing order: {str(e)}"
        )


@order_routes.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: str = Path(..., description="Order ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Delete an order."""
    try:
        logger.info(f"[ORDER_CONTROLLER] Deleting order: {order_id}")

        order_service = OrderService(db)
        success, message = order_service.delete_order(order_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)

        logger.info(f"[ORDER_CONTROLLER] Order deleted successfully: {order_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ORDER_CONTROLLER] Error deleting order: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting order: {str(e)}"
        )

