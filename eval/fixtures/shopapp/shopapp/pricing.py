"""Pricing rules. All money is integer cents — never floats."""
from shopapp.models import OrderItem

DISCOUNT_CODES = {"WELCOME10": 10, "VIP20": 20}
TAX_RATES_BPS = {"CA": 725, "NY": 400, "TX": 625}  # basis points


def compute_subtotal(items: list[OrderItem]) -> int:
    return sum(item.unit_price_cents * item.quantity for item in items)


def apply_discount(subtotal_cents: int, code: str | None) -> int:
    if not code:
        return subtotal_cents
    percent = DISCOUNT_CODES.get(code.upper(), 0)
    return subtotal_cents - (subtotal_cents * percent) // 100


def compute_tax(amount_cents: int, region: str) -> int:
    rate_bps = TAX_RATES_BPS.get(region, 0)
    return (amount_cents * rate_bps + 5_000) // 10_000


def order_total(items: list[OrderItem], region: str, code: str | None = None) -> int:
    discounted = apply_discount(compute_subtotal(items), code)
    return discounted + compute_tax(discounted, region)
