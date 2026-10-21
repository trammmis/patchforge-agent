from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.schemas.task import TaskInstance, TaskRunResult
from app.storage.task_store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def test_persists_task_status_and_result_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "patchforge.sqlite3", runs_dir=Path(tmp) / "runs")
            task = TaskInstance(
                task_id="store_demo",
                repo_path="examples/demo_python_repo",
                issue_title="Fix zero price discount bug",
                issue_body="price 0 should return 0.0",
            )
            result = TaskRunResult(
                task_id=task.task_id,
                status="succeeded",
                patch_apply_success=True,
                tests_passed=True,
                iterations=1,
                duration_seconds=0.2,
                report_path="reports/store_demo.md",
                trace_path="runs/store_demo/trace.jsonl",
                final_diff="--- a/file.py\n+++ b/file.py\n",
            )

            created = store.create_task(task)
            store.mark_queued(task.task_id, 10.0)
            store.mark_running(task.task_id, 11.0)
            store.mark_finished(task.task_id, 13.5, result)
            restored_task = store.get_task(task.task_id)
            status = store.get_status(task.task_id)
            listed = store.list_statuses()

            self.assertEqual(created.status, "created")
            self.assertEqual(restored_task.issue_title, task.issue_title)
            self.assertEqual(status.status, "succeeded")
            self.assertEqual(status.duration_seconds, 2.5)
            self.assertEqual(status.result.final_diff, result.final_diff)  # type: ignore[union-attr]
            self.assertEqual(listed[0].task_id, task.task_id)

    def test_marks_incomplete_runs_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "patchforge.sqlite3", runs_dir=Path(tmp) / "runs")
            task = TaskInstance(task_id="interrupted_demo", repo_path="repo", issue_title="Fix bug")
            store.create_task(task)
            store.mark_queued(task.task_id, 10.0)

            store.mark_interrupted_runs()
            status = store.get_status(task.task_id)

            self.assertEqual(status.status, "failed")
            self.assertEqual(status.current_phase, "interrupted")
            self.assertEqual(status.failure_type, "runner_interrupted")


if __name__ == "__main__":
    unittest.main()
