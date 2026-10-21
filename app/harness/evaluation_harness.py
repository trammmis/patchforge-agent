from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.harness.execution_harness import ExecutionHarness
from app.harness.reporter import ReportGenerator
from app.schemas.eval import EvalRunConfig, EvalRunResult, EvalTask, MetricValue
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.config import settings


class EvaluationHarness:
    def __init__(self, execution_harness: ExecutionHarness | None = None) -> None:
        settings.ensure_dirs()
        self.execution_harness = execution_harness or ExecutionHarness()
        self.reporter = ReportGenerator()

    def run(self, config: EvalRunConfig) -> EvalRunResult:
        tasks = self.load_tasks(config.dataset)
        results: list[TaskRunResult] = []
        for eval_task in tasks:
            task = TaskInstance(
                task_id=eval_task.task_id,
                repo_path=eval_task.repo_path,
                repo_url=eval_task.repo_url,
                base_commit=eval_task.base_commit,
                issue_url=eval_task.issue_url,
                issue_title=eval_task.issue_title,
                issue_body=eval_task.issue_body,
                test_command=eval_task.test_command,
                max_steps=eval_task.max_steps,
                max_iterations=eval_task.max_iterations,
                allow_network=config.allow_network or eval_task.allow_network,
                use_docker=config.use_docker or eval_task.use_docker,
                docker_image=eval_task.docker_image or config.docker_image,
                install_dependencies=config.install_dependencies or eval_task.install_dependencies,
                expected_modified_files=eval_task.expected_modified_files,
                validation_patch=eval_task.validation_patch,
                validation_test_command=eval_task.validation_test_command,
            )
            results.append(self.execution_harness.run_task(task))
        metrics = compute_metrics(results)
        report_path = self.reporter.write_eval_report(metrics, results, settings.data_dir)
        return EvalRunResult(config=config, results=results, metrics=metrics, report_path=str(report_path))

    def load_tasks(self, dataset: str | Path) -> list[EvalTask]:
        path = Path(dataset)
        tasks = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                tasks.append(EvalTask.model_validate(json.loads(line)))
        return tasks


def compute_metrics(results: list[TaskRunResult]) -> dict[str, MetricValue]:
    total = len(results) or 1
    failures = Counter(result.failure_type or "none" for result in results if result.status != "succeeded")
    return {
        "Task Success Rate": round(sum(result.status == "succeeded" for result in results) / total, 3),
        "Patch Apply Rate": round(sum(result.patch_apply_success for result in results) / total, 3),
        "Test Pass Rate": round(sum(result.tests_passed for result in results) / total, 3),
        "Regression Test Added Rate": round(
            sum(any(file.startswith("tests/") for file in result.modified_files) for result in results) / total,
            3,
        ),
        "Average Iterations": round(sum(result.iterations for result in results) / total, 3),
        "Average Runtime": round(sum(result.duration_seconds for result in results) / total, 3),
        "Average Cost": 0,
        "Failure Type Distribution": dict(failures) if failures else "none",
        "Human Intervention Rate": 0,
        "Total Cases": len(results),
    }
