from __future__ import annotations

from typing import Any

from app.schemas.base import BaseSchema, Field


class TaskInstance(BaseSchema):
    task_id: str
    repo_url: str | None = None
    repo_path: str | None = None
    base_commit: str | None = None
    issue_url: str | None = None
    issue_title: str
    issue_body: str = ""
    language: str = "python"
    test_command: str = "pytest"
    max_steps: int = 30
    max_iterations: int = 3
    timeout_seconds: int = 900
    allow_network: bool = False
    use_docker: bool = False
    docker_image: str | None = None
    install_dependencies: bool = False
    expected_modified_files: list[str] | None = None
    validation_patch: str | None = None
    validation_test_command: str | None = None


class AgentState(BaseSchema):
    task_id: str
    repo_url: str | None = None
    workspace_path: str | None = None
    raw_issue: str = ""
    parsed_issue: dict[str, Any] = Field(default_factory=dict)
    current_phase: str = "created"
    iteration_count: int = 0
    max_iterations: int = 3
    file_tree: str | None = None
    candidate_files: list[str] = Field(default_factory=list)
    opened_files: dict[str, str] = Field(default_factory=dict)
    retrieved_reflections: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_skills: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_failure_patterns: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    current_step: str | None = None
    patches: list[str] = Field(default_factory=list)
    patch_failures: list[dict[str, Any]] = Field(default_factory=list)
    approval_requests: list[dict[str, Any]] = Field(default_factory=list)
    current_diff: str | None = None
    modified_files: list[str] = Field(default_factory=list)
    test_command: str | None = None
    test_results: list[dict[str, Any]] = Field(default_factory=list)
    latest_error: str | None = None
    latest_failure_type: str | None = None
    reviewer_result: dict[str, Any] | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    final_status: str = "created"


class TaskRunResult(BaseSchema):
    task_id: str
    status: str
    patch_apply_success: bool
    tests_passed: bool
    iterations: int
    duration_seconds: float
    modified_files: list[str] = Field(default_factory=list)
    failure_type: str | None = None
    error_message: str | None = None
    final_diff: str | None = None
    report_path: str | None = None
    trace_path: str | None = None
    issue_url: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    approval_requests: list[dict[str, Any]] = Field(default_factory=list)
    environment_setup: dict[str, Any] = Field(default_factory=dict)
    experience_summary: dict[str, Any] = Field(default_factory=dict)
    evaluation_warnings: list[dict[str, Any]] = Field(default_factory=list)
