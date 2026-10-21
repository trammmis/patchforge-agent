from __future__ import annotations

from task_manager.models import Task


def filter_tasks(tasks: list[Task], include_done: bool = False) -> list[Task]:
    if include_done:
        return [task for task in tasks if not task.done]
    return [task for task in tasks if task.done]

