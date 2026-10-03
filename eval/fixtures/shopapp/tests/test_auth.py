from shopapp.auth import check_permission, hash_password, verify_password
from shopapp.models import Order, User


def test_password_roundtrip():
    stored = hash_password("hunter2")
    assert verify_password("hunter2", stored)
    assert not verify_password("hunter3", stored)


def test_order_visibility():
    alice, bob = User(1, "a@x", "h"), User(2, "b@x", "h")
    order = Order(10, user_id=1)
    assert check_permission(alice, "view_order", order)
    assert not check_permission(bob, "view_order", order)
    assert not check_permission(alice, "refund")
