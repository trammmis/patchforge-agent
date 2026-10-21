from __future__ import annotations


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    return float(ordered[midpoint])


def deduplicate(items: list[str]) -> list[str]:
    return sorted(set(items))

