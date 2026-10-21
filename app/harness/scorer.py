from __future__ import annotations

from app.schemas.task import TaskRunResult


class Scorer:
    def score(self, result: TaskRunResult) -> dict[str, bool | str]:
        return {
            "task_success": result.status == "succeeded",
            "patch_apply_success": result.patch_apply_success,
            "tests_passed": result.tests_passed,
            "failure_type": result.failure_type or "",
        }

