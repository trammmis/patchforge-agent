from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from task_manager.filters import filter_tasks
from task_manager.models import Task


class FilterTests(unittest.TestCase):
    def test_default_hides_done_tasks(self) -> None:
        tasks = [Task("open", done=False), Task("closed", done=True)]
        self.assertEqual([task.title for task in filter_tasks(tasks)], ["open"])

    def test_include_done_returns_all_tasks(self) -> None:
        tasks = [Task("open", done=False), Task("closed", done=True)]
        self.assertEqual([task.title for task in filter_tasks(tasks, include_done=True)], ["open", "closed"])


if __name__ == "__main__":
    unittest.main()

