from __future__ import annotations

from dataclasses import dataclass


VALID_PRIORITIES = {"low", "medium", "high"}


@dataclass
class Task:
    title: str
    priority: str = "medium"
    done: bool = False


def validate_priority(priority: str) -> str:
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority: {priority}")
    return priority

