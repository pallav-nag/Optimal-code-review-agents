# Contributing to shopapp

## Database access
All SQL must go through `db.query` / `db.execute` with bound `?` parameters.
Never build SQL with f-strings, `%` or `.format()` — user input reaches these queries.

## Money
Money is always an integer number of cents. Never use `float` for prices, totals,
discounts or tax; use integer arithmetic with explicit rounding (see `compute_tax`).

## Outbound HTTP
Every outbound HTTP call (payments, webhooks) must pass an explicit `timeout=`.
Payment failures must raise `PaymentError`; never swallow exceptions from the provider.

## Authorization
Handlers that read or mutate an order must call `auth.check_permission(user, action, order)`
before returning order data. Admin-only actions are listed in `auth.ADMIN_ACTIONS`.

## Passwords
Passwords are hashed with PBKDF2-HMAC-SHA256 (`auth.hash_password`). Do not use MD5/SHA1.
