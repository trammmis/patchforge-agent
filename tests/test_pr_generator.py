from __future__ import annotations

import unittest

from app.agents.pr_generator import PRGenerator


class PRGeneratorTests(unittest.TestCase):
    def test_includes_linked_issue_when_available(self) -> None:
        result = PRGenerator().generate(
            "Fix settings parser",
            "--- a/file.py\n+++ b/file.py\n",
            {"command": "python -m unittest", "status": "passed"},
            {"changed_files": ["src/task_manager/config.py"], "summary": "ok", "risk": "low"},
            issue_url="https://github.com/example/project/issues/5",
        )

        self.assertIn("## 关联 Issue", result["body"])
        self.assertIn("https://github.com/example/project/issues/5", result["body"])


if __name__ == "__main__":
    unittest.main()
