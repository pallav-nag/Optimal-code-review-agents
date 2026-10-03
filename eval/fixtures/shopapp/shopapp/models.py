from dataclasses import dataclass, field


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    role: str = "customer"


@dataclass
class Product:
    id: int
    name: str
    category: str
    price_cents: int
    stock: int = 0


@dataclass
class OrderItem:
    product_id: int
    quantity: int
    unit_price_cents: int


@dataclass
class Order:
    id: int
    user_id: int
    items: list[OrderItem] = field(default_factory=list)
    status: str = "pending"
    total_cents: int = 0
