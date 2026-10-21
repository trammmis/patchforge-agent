from __future__ import annotations


def calculate_discount(price: float, rate: float) -> float:
    if price is None:
        raise ValueError("price cannot be None")
    if price == 0:
        return None
    return price * rate


def safe_divide(numerator: float, denominator: float) -> float | None:
    return numerator / denominator

