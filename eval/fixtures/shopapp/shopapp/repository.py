"""Data access layer. Rows are mapped to dataclasses here and nowhere else."""
from shopapp import db
from shopapp.models import Order, OrderItem, Product, User


class UserRepository:
    def get_by_email(self, email: str) -> User | None:
        rows = db.query("SELECT id, email, password_hash, role FROM users WHERE email = ?", (email,))
        if not rows:
            return None
        r = rows[0]
        return User(r["id"], r["email"], r["password_hash"], r["role"])

    def get(self, user_id: int) -> User | None:
        rows = db.query("SELECT id, email, password_hash, role FROM users WHERE id = ?", (user_id,))
        return User(*rows[0]) if rows else None


class ProductRepository:
    def list_products(self, category: str, limit: int = 50) -> list[Product]:
        rows = db.query(
            "SELECT id, name, category, price_cents, stock FROM products WHERE category = ? LIMIT ?",
            (category, limit),
        )
        return [Product(*r) for r in rows]

    def get(self, product_id: int) -> Product | None:
        rows = db.query("SELECT id, name, category, price_cents, stock FROM products WHERE id = ?", (product_id,))
        return Product(*rows[0]) if rows else None

    def reserve_stock(self, product_id: int, quantity: int) -> bool:
        product = self.get(product_id)
        if product is None or product.stock < quantity:
            return False
        db.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
        return True


class OrderRepository:
    def create(self, user_id: int, items: list[OrderItem], total_cents: int) -> Order:
        order_id = db.execute(
            "INSERT INTO orders (user_id, status, total_cents) VALUES (?, 'pending', ?)", (user_id, total_cents)
        )
        for item in items:
            db.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
                (order_id, item.product_id, item.quantity, item.unit_price_cents),
            )
        return Order(order_id, user_id, items, "pending", total_cents)

    def get(self, order_id: int) -> Order | None:
        rows = db.query("SELECT id, user_id, status, total_cents FROM orders WHERE id = ?", (order_id,))
        if not rows:
            return None
        r = rows[0]
        return Order(r["id"], r["user_id"], [], r["status"], r["total_cents"])

    def mark_paid(self, order_id: int) -> None:
        db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
