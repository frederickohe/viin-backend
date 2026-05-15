import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime, String, ForeignKey, Text, Numeric, Integer
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from utilities.dbconfig import Base


class Product(Base):
    """
    Product model for managing product catalog.
    Stores storefront details and optional stock/link metadata.
    """
    __tablename__ = "products"

    # Primary Identifiers
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    inventory_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        String(20),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # Product Details
    photo: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    condition: Mapped[str] = mapped_column(String(100), nullable=False)
    number_in_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ordered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Product(product_id={self.product_id}, inventory_id={self.inventory_id}, name={self.name})>"


class Inventory(Base):
    """
    Inventory model for managing product stock levels.
    Tracks quantities across locations and provides AI-optimized stock recommendations.
    """
    __tablename__ = "inventory"

    # Primary Identifiers
    inventory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.product_id"),
        nullable=False,
        index=True
    )
    location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Quantities
    quantity_on_hand: Mapped[int] = mapped_column(default=0, nullable=False)
    quantity_reserved: Mapped[int] = mapped_column(default=0, nullable=False)  # Reserved for orders
    # quantity_available is calculated as: quantity_on_hand - quantity_reserved
    quantity_in_transit: Mapped[int] = mapped_column(default=0, nullable=False)
    quantity_on_order: Mapped[int] = mapped_column(default=0, nullable=False)  # Ordered but not received
    quantity_backordered: Mapped[int] = mapped_column(default=0, nullable=False)

    # Stock Levels
    min_stock_level: Mapped[Optional[int]] = mapped_column(nullable=True)  # Safety stock
    max_stock_level: Mapped[Optional[int]] = mapped_column(nullable=True)  # Storage capacity
    reorder_point: Mapped[Optional[int]] = mapped_column(nullable=True)  # Trigger reorder at this level
    reorder_quantity: Mapped[Optional[int]] = mapped_column(nullable=True)  # How many to reorder

    # AI-Optimized Fields
    optimal_stock_level: Mapped[Optional[int]] = mapped_column(nullable=True)  # AI-calculated optimal stock
    stockout_risk_score: Mapped[Optional[float]] = mapped_column(nullable=True)  # Probability of stockout (0-100)
    days_of_inventory: Mapped[Optional[int]] = mapped_column(nullable=True)  # Days until stockout at current rate

    # Timestamps
    last_counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reordered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_reorder_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # AI-predicted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def quantity_available(self) -> int:
        """Calculate available quantity (on-hand minus reserved)."""
        return max(0, self.quantity_on_hand - self.quantity_reserved)

    def __repr__(self):
        return f"<Inventory(inventory_id={self.inventory_id}, product_id={self.product_id}, location={self.location}, available={self.quantity_available})>"
