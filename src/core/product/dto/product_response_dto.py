"""Product Response DTO"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ProductResponseDTO(BaseModel):
    """Response model for product details."""
    
    # Primary Identifiers
    product_id: str
    inventory_id: str
    user_id: Optional[str] = None
    
    # Product Details
    photo: str
    name: str
    description: Optional[str] = None
    price: float
    category: Optional[str] = None
    condition: str
    number_in_stock: Optional[int] = None
    link: Optional[str] = None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    last_sold_at: Optional[datetime] = None
    last_ordered_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "product_id": "550e8400-e29b-41d4-a716-446655440000",
                "inventory_id": "PROD-WIRELESS-HEADPHONES-001",
                "photo": "https://cdn.example.com/products/wireless-headphones.jpg",
                "name": "Premium Wireless Headphones",
                "description": "High-quality wireless headphones with noise cancellation",
                "price": 199.99,
                "category": "Electronics",
                "condition": "New",
                "number_in_stock": 10,
                "link": "https://shop.example.com/products/wireless-headphones",
                "created_at": "2026-03-20T10:30:00Z",
                "updated_at": "2026-03-21T08:00:00Z",
                "last_sold_at": "2026-03-21T07:30:00Z",
                "last_ordered_at": "2026-03-21T06:00:00Z"
            }
        }

    @classmethod
    def from_product(cls, product):
        """Convert Product model to response DTO."""
        return cls(
            product_id=str(product.product_id),
            inventory_id=product.inventory_id,
            user_id=getattr(product, "user_id", None),
            photo=product.photo,
            name=product.name,
            description=product.description,
            price=float(product.price),
            category=product.category,
            condition=product.condition,
            number_in_stock=product.number_in_stock,
            link=product.link,
            created_at=product.created_at,
            updated_at=product.updated_at,
            last_sold_at=product.last_sold_at,
            last_ordered_at=product.last_ordered_at
        )
