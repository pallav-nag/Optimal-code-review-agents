"""Password hashing, sessions and permission checks."""
import hashlib
import hmac
import os
import secrets

from shopapp.models import Order, User

PBKDF2_ROUNDS = 310_000
ADMIN_ACTIONS = {"refund", "view_any_order"}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def check_permission(user: User, action: str, order: Order | None = None) -> bool:
    if user.role == "admin":
        return True
    if action in ADMIN_ACTIONS:
        return False
    if order is not None:
        return order.user_id == user.id
    return True
