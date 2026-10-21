from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.harness.evaluation_harness import EvaluationHarness
from app.schemas.eval import EvalRunConfig
from app.schemas.task import TaskInstance, TaskRunResult


class _FakeExecutionHarness:
    def __init__(self) -> None:
        self.tasks: list[TaskInstance] = []

    def run_task(self, task: TaskInstance) -> TaskRunResult:
        self.tasks.append(task)
        return TaskRunResult(
            task_id=task.task_id,
            status="succeeded",
            patch_apply_success=True,
            tests_passed=True,
            iterations=1,
            duration_seconds=0.1,
            modified_files=task.expected_modified_files or [],
        )


class _FailingExecutionHarness:
    def __init__(self) -> None:
        self.tasks: list[TaskInstance] = []

    def run_task(self, task: TaskInstance) -> TaskRunResult:
        self.tasks.append(task)
        return TaskRunResult(
            task_id=task.task_id,
            status="failed",
            patch_apply_success=True,
            tests_passed=False,
            iterations=2,
            duration_seconds=0.2,
            modified_files=["src/app.py"],
            failure_type="logic_error",
            error_message="regression test failed",
        )


class EvaluationHarnessTests(unittest.TestCase):
    def test_remote_eval_task_fields_are_passed_to_execution_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "real_tasks.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "task_id": "pytest-dev__pytest-7432",
                        "repo_url": "https://github.com/pytest-dev/pytest",
                        "base_commit": "e6e300e729dd33956e5448d8be9a0b1540b4e53a",
                        "issue_url": "https://github.com/pytest-dev/pytest/issues/7392",
                        "issue_title": "skipping: --runxfail breaks pytest.mark.skip location reporting",
                        "issue_body": "The skip location should point to the test item.",
                        "test_command": "python -m pytest testing/test_skipping.py",
                        "expected_modified_files": ["src/_pytest/skipping.py"],
                        "use_docker": True,
                        "docker_image": "python:3.8-slim",
                        "install_dependencies": True,
                        "allow_network": True,
                        "max_iterations": 3,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            fake_execution = _FakeExecutionHarness()

            result = EvaluationHarness(execution_harness=fake_execution).run(EvalRunConfig(dataset=str(dataset)))

            self.assertEqual(result.metrics["Task Success Rate"], 1.0)
            task = fake_execution.tasks[0]
            self.assertEqual(task.repo_url, "https://github.com/pytest-dev/pytest")
            self.assertIsNone(task.repo_path)
            self.assertEqual(task.base_commit, "e6e300e729dd33956e5448d8be9a0b1540b4e53a")
            self.assertEqual(task.issue_url, "https://github.com/pytest-dev/pytest/issues/7392")
            self.assertTrue(task.use_docker)
            self.assertEqual(task.docker_image, "python:3.8-slim")
            self.assertTrue(task.install_dependencies)
            self.assertTrue(task.allow_network)
            self.assertEqual(task.expected_modified_files, ["src/_pytest/skipping.py"])

    def test_failure_type_distribution_accepts_dict_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "failing_tasks.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "task_id": "failing_task",
                        "repo_path": "examples/demo_python_repo",
                        "issue_title": "broken behavior",
                        "issue_body": "fails focused regression test",
                        "test_command": "pytest",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = EvaluationHarness(execution_harness=_FailingExecutionHarness()).run(
                EvalRunConfig(dataset=str(dataset))
            )

            self.assertEqual(result.metrics["Task Success Rate"], 0.0)
            self.assertEqual(result.metrics["Failure Type Distribution"], {"logic_error": 1})

    def test_load_tasks_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "bom_tasks.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "task_id": "bom_task",
                        "repo_path": "examples/demo_python_repo",
                        "issue_title": "broken behavior",
                        "issue_body": "fails focused regression test",
                        "test_command": "pytest",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )

            tasks = EvaluationHarness(execution_harness=_FakeExecutionHarness()).load_tasks(dataset)

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].task_id, "bom_task")


if __name__ == "__main__":
    unittest.main()
