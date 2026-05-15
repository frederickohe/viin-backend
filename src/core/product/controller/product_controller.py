"""Product Controller"""
from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.orm import Session
from typing import List
import logging

from core.product.service.product_service import ProductService
from core.product.dto.product_response_dto import ProductResponseDTO
from core.product.dto.product_create_dto import ProductCreateDTO
from core.product.dto.product_update_dto import ProductUpdateDTO
from core.product.dto.inventory_response_dto import InventoryResponseDTO
from core.product.dto.inventory_create_dto import InventoryCreateDTO
from core.product.dto.inventory_update_dto import InventoryUpdateDTO
from core.user.controller.usercontroller import validate_token, get_db
from another_fastapi_jwt_auth import AuthJWT

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

product_routes = APIRouter()


# ==================== PRODUCT ENDPOINTS ====================

@product_routes.get("/by-name/{product_name}", response_model=List[ProductResponseDTO])
def get_product_by_name(
    product_name: str = Path(..., description="Product name phrase to search for"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all products containing the name phrase (case-insensitive search)."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Searching products by name: {product_name}")

        product_service = ProductService(db)
        products = product_service.get_product_by_name(product_name, skip, limit)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(products)} products matching '{product_name}'")
        return [ProductResponseDTO.from_product(p) for p in products]

    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error getting products by name: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving products: {str(e)}"
        )


@product_routes.get("/user/{user_id}", response_model=List[ProductResponseDTO])
def list_products_by_user(
    user_id: str = Path(..., description="User ID, email, or phone number"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    category: str = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """Get all products for a particular user."""
    _ = authjwt
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Listing products for user: {user_id}")

        product_service = ProductService(db)
        products = product_service.get_products_by_user(user_id, skip, limit, category)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(products)} products for user {user_id}")
        return [ProductResponseDTO.from_product(p) for p in products]

    except Exception as e:
        logger.error(
            f"[PRODUCT_CONTROLLER] Error listing products for user: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving products for user: {str(e)}",
        )


@product_routes.post("/", response_model=ProductResponseDTO)
def create_product(
    request: ProductCreateDTO,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Create a new product. Inventory is automatically created."""
    try:
        owner_id = authjwt.get_jwt_subject()
        logger.info(f"[PRODUCT_CONTROLLER] Creating product: {request.name} for user: {owner_id}")

        product_service = ProductService(db)
        success, product, message = product_service.create_product(request, user_id=owner_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Product created successfully: {product.inventory_id}")
        return ProductResponseDTO.from_product(product)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error creating product: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating product: {str(e)}"
        )


@product_routes.get("/{product_id}", response_model=ProductResponseDTO)
def get_product(
    product_id: str = Path(..., description="Product ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get a specific product by ID."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Getting product: {product_id}")

        product_service = ProductService(db)
        product = product_service.get_product_by_id(product_id)

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )

        return ProductResponseDTO.from_product(product)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error getting product: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving product: {str(e)}"
        )

@product_routes.get("/", response_model=List[ProductResponseDTO])
def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    category: str = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all products with optional filtering."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Listing all products")

        product_service = ProductService(db)
        products = product_service.get_all_products(skip, limit, category)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(products)} products")
        return [ProductResponseDTO.from_product(p) for p in products]

    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error listing products: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving products: {str(e)}"
        )


@product_routes.put("/{product_id}", response_model=ProductResponseDTO)
def update_product(
    product_id: str = Path(..., description="Product ID"),
    request: ProductUpdateDTO = None,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Update an existing product."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Updating product: {product_id}")

        if not request:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No update data provided"
            )

        product_service = ProductService(db)
        success, product, message = product_service.update_product(product_id, request)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Product updated successfully: {product.inventory_id}")
        return ProductResponseDTO.from_product(product)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error updating product: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating product: {str(e)}"
        )


@product_routes.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: str = Path(..., description="Product ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Delete a product."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Deleting product: {product_id}")

        product_service = ProductService(db)
        success, message = product_service.delete_product(product_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Product deleted successfully: {product_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error deleting product: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting product: {str(e)}"
        )


# ==================== INVENTORY ENDPOINTS ====================

@product_routes.post("/inventory", response_model=InventoryResponseDTO)
def create_inventory(
    request: InventoryCreateDTO,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Create inventory for a product."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Creating inventory for product: {request.product_id}")

        product_service = ProductService(db)
        success, inventory, message = product_service.create_inventory(request)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Inventory created successfully")
        return InventoryResponseDTO.from_inventory(inventory)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error creating inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating inventory: {str(e)}"
        )


@product_routes.get("/inventory/{inventory_id}", response_model=InventoryResponseDTO)
def get_inventory(
    inventory_id: str = Path(..., description="Inventory ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get specific inventory record."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Getting inventory: {inventory_id}")

        product_service = ProductService(db)
        inventory = product_service.get_inventory_by_id(inventory_id)

        if not inventory:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Inventory not found"
            )

        return InventoryResponseDTO.from_inventory(inventory)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error getting inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving inventory: {str(e)}"
        )


@product_routes.get("/{product_id}/inventory", response_model=List[InventoryResponseDTO])
def get_product_inventory(
    product_id: str = Path(..., description="Product ID"),
    location: str = Query(None, description="Filter by location"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all inventory records for a product."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Getting inventory for product: {product_id}")

        product_service = ProductService(db)
        inventory = product_service.get_product_inventory(product_id, location)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(inventory)} inventory records")
        return [InventoryResponseDTO.from_inventory(i) for i in inventory]

    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error getting inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving inventory: {str(e)}"
        )


@product_routes.get("/inventory", response_model=List[InventoryResponseDTO])
def list_all_inventory(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get all inventory records."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Listing all inventory")

        product_service = ProductService(db)
        inventory = product_service.get_all_inventory(skip, limit)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(inventory)} inventory records")
        return [InventoryResponseDTO.from_inventory(i) for i in inventory]

    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error listing inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving inventory: {str(e)}"
        )


@product_routes.put("/inventory/{inventory_id}", response_model=InventoryResponseDTO)
def update_inventory(
    inventory_id: str = Path(..., description="Inventory ID"),
    request: InventoryUpdateDTO = None,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Update inventory record."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Updating inventory: {inventory_id}")

        if not request:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No update data provided"
            )

        product_service = ProductService(db)
        success, inventory, message = product_service.update_inventory(inventory_id, request)

        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Inventory updated successfully: {inventory_id}")
        return InventoryResponseDTO.from_inventory(inventory)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error updating inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating inventory: {str(e)}"
        )


@product_routes.delete("/inventory/{inventory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inventory(
    inventory_id: str = Path(..., description="Inventory ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Delete inventory record."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Deleting inventory: {inventory_id}")

        product_service = ProductService(db)
        success, message = product_service.delete_inventory(inventory_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)

        logger.info(f"[PRODUCT_CONTROLLER] Inventory deleted successfully: {inventory_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error deleting inventory: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting inventory: {str(e)}"
        )


@product_routes.get("/inventory/low-stock", response_model=List[InventoryResponseDTO])
def get_low_stock_items(
    threshold: float = Query(0.5, ge=0, le=1, description="Threshold multiplier (default 0.5 = 50% of reorder point)"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """Get inventory items below reorder point threshold."""
    try:
        logger.info(f"[PRODUCT_CONTROLLER] Getting low stock items with threshold: {threshold}")

        product_service = ProductService(db)
        inventory = product_service.get_low_stock_items(threshold)

        logger.info(f"[PRODUCT_CONTROLLER] Found {len(inventory)} low stock items")
        return [InventoryResponseDTO.from_inventory(i) for i in inventory]

    except Exception as e:
        logger.error(f"[PRODUCT_CONTROLLER] Error getting low stock items: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving low stock items: {str(e)}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BENEFICIARY_CONTROLLER] Error updating product: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating product: {str(e)}"
        )
