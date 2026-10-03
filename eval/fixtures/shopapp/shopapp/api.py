"""HTTP handlers (framework-agnostic: each takes a request dict and returns a response dict)."""
from shopapp import auth, pricing
from shopapp.models import OrderItem
from shopapp.payments import PaymentError, PaymentGateway
from shopapp.repository import OrderRepository, ProductRepository, UserRepository
from shopapp.utils import paginate, safe_int

users = UserRepository()
products = ProductRepository()
orders = OrderRepository()
gateway = PaymentGateway(api_key="")


def login(request: dict) -> dict:
    user = users.get_by_email(request["email"])
    if user is None or not auth.verify_password(request["password"], user.password_hash):
        return {"status": 401, "error": "invalid credentials"}
    return {"status": 200, "token": auth.create_session_token()}


def search_products(request: dict) -> dict:
    items = products.list_products(request.get("category", "all"), limit=200)
    page = paginate(items, safe_int(request.get("page"), 1), safe_int(request.get("size"), 20))
    return {"status": 200, "products": [p.__dict__ for p in page]}


def checkout(request: dict, user) -> dict:
    items = []
    for line in request["items"]:
        product = products.get(line["product_id"])
        if product is None:
            return {"status": 404, "error": f"unknown product {line['product_id']}"}
        if not products.reserve_stock(product.id, line["quantity"]):
            return {"status": 409, "error": f"insufficient stock for {product.name}"}
        items.append(OrderItem(product.id, line["quantity"], product.price_cents))
    total = pricing.order_total(items, request.get("region", ""), request.get("discount_code"))
    order = orders.create(user.id, items, total)
    try:
        gateway.charge(order, request["card_token"])
    except PaymentError:
        return {"status": 402, "error": "payment failed", "order_id": order.id}
    orders.mark_paid(order.id)
    return {"status": 201, "order_id": order.id, "total_cents": total}


def get_order(request: dict, user, order_id: int) -> dict:
    order = orders.get(order_id)
    if order is None:
        return {"status": 404, "error": "not found"}
    if not auth.check_permission(user, "view_order", order):
        return {"status": 403, "error": "forbidden"}
    return {"status": 200, "order": order.__dict__}


def refund_order(request: dict, user, order_id: int) -> dict:
    if not auth.check_permission(user, "refund"):
        return {"status": 403, "error": "forbidden"}
    order = orders.get(order_id)
    if order is None:
        return {"status": 404, "error": "not found"}
    gateway.refund(request["charge_id"], order.total_cents)
    return {"status": 200}
