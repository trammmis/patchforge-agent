from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.harness.trace_recorder import TraceRecorder
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.task_runner import TaskRunner
from app.storage.task_store import TaskStore


class FakeHarness:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def run_task(self, task: TaskInstance) -> TaskRunResult:
        trace = TraceRecorder(self.runs_dir / task.task_id / "trace.jsonl")
        trace.record("task_started", task.model_dump())
        trace.record("agent_phase", {"phase": "parse_issue"})
        time.sleep(0.01)
        result = TaskRunResult(
            task_id=task.task_id,
            status="succeeded",
            patch_apply_success=True,
            tests_passed=True,
            iterations=1,
            duration_seconds=0.01,
            trace_path=str(self.runs_dir / task.task_id / "trace.jsonl"),
            final_diff="--- a/app.py\n+++ b/app.py\n",
        )
        trace.record("task_finished", result.model_dump())
        return result

    def resume_after_approval(
        self,
        task: TaskInstance,
        previous_result: TaskRunResult,
        *,
        approved: bool,
        reviewer: str = "human",
        notes: str = "",
    ) -> TaskRunResult:
        return TaskRunResult(
            task_id=task.task_id,
            status="succeeded" if approved else "failed",
            patch_apply_success=approved,
            tests_passed=approved,
            iterations=previous_result.iterations,
            duration_seconds=0.01,
            trace_path=previous_result.trace_path,
            final_diff=previous_result.final_diff,
            approval_requests=[
                {
                    **previous_result.approval_requests[0],
                    "approved": approved,
                    "reviewer": reviewer,
                    "notes": notes,
                }
            ],
        )


class TaskRunnerTests(unittest.TestCase):
    def test_background_runner_tracks_status_events_and_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            runner = TaskRunner(
                harness_factory=lambda: FakeHarness(runs_dir),  # type: ignore[arg-type]
                runs_dir=runs_dir,
                task_store=TaskStore(Path(tmp) / "patchforge.sqlite3", runs_dir=runs_dir),
                max_workers=1,
            )
            task = TaskInstance(task_id="runner_demo", repo_path="repo", issue_title="Fix bug")

            created = runner.register(task)
            started = runner.start(task.task_id)
            finished = runner.wait(task.task_id, timeout=2)

            self.assertEqual(created.status, "created")
            self.assertIn(started.status, {"queued", "running"})
            self.assertEqual(finished.status, "succeeded")
            self.assertEqual(finished.current_phase, "finished")
            self.assertEqual(finished.iteration_count, 1)
            self.assertTrue(runner.get_events(task.task_id))
            self.assertTrue(runner.get_timeline(task.task_id).terminal)
            self.assertIn("app.py", runner.get_result(task.task_id).final_diff or "")  # type: ignore[union-attr]
            runner.close()

    def test_runner_can_restore_finished_task_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            db_path = Path(tmp) / "patchforge.sqlite3"
            store = TaskStore(db_path, runs_dir=runs_dir)
            runner = TaskRunner(
                harness_factory=lambda: FakeHarness(runs_dir),  # type: ignore[arg-type]
                runs_dir=runs_dir,
                task_store=store,
                max_workers=1,
            )
            task = TaskInstance(task_id="restored_demo", repo_path="repo", issue_title="Fix bug")
            runner.register(task)
            runner.start(task.task_id)
            runner.wait(task.task_id, timeout=2)

            restored = TaskRunner(
                harness_factory=lambda: FakeHarness(runs_dir),  # type: ignore[arg-type]
                runs_dir=runs_dir,
                task_store=TaskStore(db_path, runs_dir=runs_dir),
                max_workers=1,
            )

            self.assertEqual(restored.get_task(task.task_id).issue_title, "Fix bug")
            self.assertEqual(restored.get_status(task.task_id).status, "succeeded")
            self.assertTrue(restored.get_status(task.task_id).result)
            runner.close()
            restored.close()

    def test_runner_approval_updates_persisted_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            db_path = Path(tmp) / "patchforge.sqlite3"
            runner = TaskRunner(
                harness_factory=lambda: FakeHarness(runs_dir),  # type: ignore[arg-type]
                runs_dir=runs_dir,
                task_store=TaskStore(db_path, runs_dir=runs_dir),
                max_workers=1,
            )
            task = TaskInstance(task_id="approval_runner", repo_path="repo", issue_title="Approve patch")
            previous = TaskRunResult(
                task_id=task.task_id,
                status="needs_approval",
                patch_apply_success=False,
                tests_passed=False,
                iterations=1,
                duration_seconds=0.01,
                trace_path=str(runs_dir / task.task_id / "trace.jsonl"),
                final_diff="--- a/requirements.txt\n+++ b/requirements.txt\n",
                approval_requests=[{"diff": "--- a/requirements.txt\n+++ b/requirements.txt\n", "approved": False}],
            )
            runner.register(task)
            runner.store.mark_finished(task.task_id, time.time(), previous)
            runner._records[task.task_id].result = previous
            runner._records[task.task_id].status = previous.status

            status = runner.approve(task.task_id, approved=True, reviewer="tester", notes="ok")
            restored = TaskStore(db_path, runs_dir=runs_dir).get_status(task.task_id)

            self.assertEqual(status.status, "succeeded")
            self.assertTrue(restored.result.approval_requests[0]["approved"])  # type: ignore[union-attr]
            runner.close()


if __name__ == "__main__":
    unittest.main()
