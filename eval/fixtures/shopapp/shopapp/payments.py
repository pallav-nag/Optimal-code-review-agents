"""Payment provider client."""
import logging

import requests

from shopapp.models import Order

log = logging.getLogger(__name__)
PROVIDER_URL = "https://payments.example.com/v1"


class PaymentError(Exception):
    pass


class PaymentGateway:
    def __init__(self, api_key: str, max_retries: int = 2):
        self.api_key = api_key
        self.max_retries = max_retries

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def charge(self, order: Order, card_token: str) -> str:
        payload = {"amount": order.total_cents, "currency": "usd", "source": card_token, "reference": order.id}
        last_error = None
        for _attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(f"{PROVIDER_URL}/charges", json=payload, headers=self._headers(), timeout=10)
                resp.raise_for_status()
                return resp.json()["charge_id"]
            except requests.RequestException as e:
                last_error = e
                log.warning("charge attempt failed for order %s: %s", order.id, e)
        raise PaymentError(f"charge failed for order {order.id}") from last_error

    def refund(self, charge_id: str, amount_cents: int) -> bool:
        try:
            resp = requests.post(
                f"{PROVIDER_URL}/refunds",
                json={"charge": charge_id, "amount": amount_cents},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            log.error("refund failed for %s: %s", charge_id, e)
            raise PaymentError(str(e)) from e
