"""A tiny example application; no external services or real customer data."""


def invoice_total(unit_price_cents, quantity):
    if unit_price_cents < 0 or quantity < 0:
        raise ValueError("Price and quantity must be nonnegative")
    return unit_price_cents * quantity
