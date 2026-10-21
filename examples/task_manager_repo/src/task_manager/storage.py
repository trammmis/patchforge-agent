from __future__ import annotations

import json
from pathlib import Path

from task_manager.models import Task


def save_tasks(tasks: list[Task], path: str | Path) -> None:
    target = Path(path)
    payload = [task.__dict__ for task in tasks]
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

