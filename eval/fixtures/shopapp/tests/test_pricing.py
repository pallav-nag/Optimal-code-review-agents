from shopapp.models import OrderItem
from shopapp.pricing import apply_discount, compute_subtotal, compute_tax, order_total


def test_subtotal():
    assert compute_subtotal([OrderItem(1, 2, 500), OrderItem(2, 1, 250)]) == 1250


def test_discount_codes():
    assert apply_discount(1000, "welcome10") == 900
    assert apply_discount(1000, None) == 1000
    assert apply_discount(1000, "BOGUS") == 1000


def test_tax_rounds_half_up():
    assert compute_tax(1000, "CA") == 73


def test_order_total():
    assert order_total([OrderItem(1, 1, 1000)], "NY", "VIP20") == 832
