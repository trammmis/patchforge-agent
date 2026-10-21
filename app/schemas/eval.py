from __future__ import annotations

from app.schemas.base import BaseSchema, Field
from app.schemas.task import TaskRunResult

MetricValue = float | int | str | dict[str, int]


class EvalTask(BaseSchema):
    task_id: str
    repo_path: str | None = None
    repo_url: str | None = None
    base_commit: str | None = None
    issue_url: str | None = None
    issue_title: str
    issue_body: str = ""
    test_command: str = "pytest"
    expected_modified_files: list[str] | None = None
    max_steps: int = 20
    max_iterations: int = 3
    use_docker: bool = False
    install_dependencies: bool = False
    allow_network: bool = False
    docker_image: str | None = None
    validation_patch: str | None = None
    validation_test_command: str | None = None


class EvalRunConfig(BaseSchema):
    dataset: str = "evals/tasks.jsonl"
    model: str = "offline-rule-agent"
    policy_version: str = "v1"
    use_docker: bool = False
    install_dependencies: bool = False
    allow_network: bool = False
    docker_image: str | None = None


class EvalRunResult(BaseSchema):
    config: EvalRunConfig
    results: list[TaskRunResult] = Field(default_factory=list)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    report_path: str | None = None
