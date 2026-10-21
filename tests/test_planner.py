from __future__ import annotations

import unittest

from app.agents.planner import Planner
from app.schemas.issue import ParsedIssue


class PlannerTests(unittest.TestCase):
    def test_injects_retrieved_experience_into_plan(self) -> None:
        issue = ParsedIssue(summary="Validate priority should be case-insensitive")

        plan = Planner().plan(
            issue,
            ["src/task_manager/models.py", "tests/test_priority.py"],
            "python -m unittest tests.test_priority",
            retrieved_reflections=[
                {
                    "task_id": "taskmgr_priority_uppercase",
                    "reflection": "taskmgr_priority_uppercase succeeded by changing src/task_manager/models.py.",
                }
            ],
            retrieved_skills=[
                {
                    "skill_id": "skill_taskmgr_priority_uppercase",
                    "name": "Repair pattern from taskmgr_priority_uppercase",
                    "procedure": ["Inspect priority validation", "Normalize accepted inputs"],
                }
            ],
            retrieved_failure_patterns=[
                {
                    "failure_type": "patch_apply_failed",
                    "recommended_recovery": ["Regenerate diff from current content"],
                }
            ],
        )

        self.assertIn("Apply relevant reflection memory", plan[0])
        self.assertTrue(any("Reuse validated skill" in step for step in plan))
        self.assertTrue(any("Avoid known failure pattern" in step for step in plan))
        self.assertTrue(any("Use existing tests as regression oracle" in step for step in plan))


if __name__ == "__main__":
    unittest.main()
