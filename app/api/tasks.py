from __future__ import annotations

import uuid
from typing import Any

try:
    from fastapi import HTTPException
except Exception:  # pragma: no cover
    HTTPException = None

from app.harness.evaluation_harness import EvaluationHarness
from app.schemas.base import BaseSchema
from app.schemas.eval import EvalRunConfig
from app.schemas.task import TaskInstance
from app.services.github import GitHubIssueError, fetch_issue
from app.services.task_runner import TaskRunner

TASK_RUNNER = TaskRunner()


class CreateTaskRequest(BaseSchema):
    repo_path: str | None = None
    repo_url: str | None = None
    issue_url: str | None = None
    issue_title: str = "Local PatchForge task"
    issue_body: str = ""
    base_commit: str | None = None
    test_command: str = "pytest"
    mode: str = "dry_run"
    use_docker: bool = False
    docker_image: str | None = None
    install_dependencies: bool = False
    allow_network: bool = False


class ApprovalDecisionRequest(BaseSchema):
    approved: bool
    reviewer: str = "human"
    notes: str = ""


def attach_routes(app: Any) -> None:
    @app.get("/tasks")
    def list_tasks(limit: int = 50) -> list[dict]:
        return [status.model_dump() for status in TASK_RUNNER.list_statuses(limit=limit)]

    @app.post("/tasks")
    def create_task(payload: CreateTaskRequest) -> dict[str, str]:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        issue_title = payload.issue_title
        issue_body = payload.issue_body
        repo_url = payload.repo_url
        issue_url = payload.issue_url
        if issue_url:
            try:
                issue = fetch_issue(issue_url)
            except GitHubIssueError as exc:
                if HTTPException is not None:
                    raise HTTPException(status_code=400, detail=str(exc))
                raise
            issue_title = issue_title if issue_title != "Local PatchForge task" else issue["issue_title"]
            issue_body = issue_body or issue["issue_body"]
            repo_url = repo_url or issue.get("repo_url")
            issue_url = issue["issue_url"]
        task = TaskInstance(
            task_id=task_id,
            repo_url=repo_url,
            repo_path=payload.repo_path,
            base_commit=payload.base_commit,
            issue_url=issue_url,
            issue_title=issue_title,
            issue_body=issue_body,
            test_command=payload.test_command,
            allow_network=payload.allow_network,
            use_docker=payload.use_docker,
            docker_image=payload.docker_image,
            install_dependencies=payload.install_dependencies,
        )
        TASK_RUNNER.register(task)
        return {"task_id": task_id, "status": "created"}

    @app.post("/tasks/{task_id}/run")
    def run_task(task_id: str) -> dict:
        _get_task(task_id)
        return TASK_RUNNER.start(task_id).model_dump()

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = _get_task(task_id)
        status = TASK_RUNNER.get_status(task_id).model_dump()
        status["task"] = task.model_dump()
        if status.get("result"):
            status["environment_setup"] = status["result"].get("environment_setup")
        else:
            status["environment_setup"] = None
        return status

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str) -> list[dict]:
        _get_task(task_id)
        return TASK_RUNNER.get_events(task_id)

    @app.get("/tasks/{task_id}/timeline")
    def get_timeline(task_id: str) -> dict:
        _get_task(task_id)
        return TASK_RUNNER.get_timeline(task_id).model_dump()

    @app.get("/tasks/{task_id}/diff")
    def get_diff(task_id: str) -> dict[str, str]:
        result = TASK_RUNNER.get_result(task_id)
        return {"diff": result.final_diff if result else ""}

    @app.post("/tasks/{task_id}/approve-patch")
    @app.post("/tasks/{task_id}/approve-pr")
    def approve_patch(task_id: str, payload: ApprovalDecisionRequest) -> dict:
        _get_task(task_id)
        try:
            status = TASK_RUNNER.approve(
                task_id,
                approved=payload.approved,
                reviewer=payload.reviewer,
                notes=payload.notes,
            )
        except ValueError as exc:
            if HTTPException is not None:
                raise HTTPException(status_code=400, detail=str(exc))
            raise
        result = status.model_dump()
        result["approved"] = payload.approved
        result["message"] = "审批已处理；批准时会应用待审 patch 并继续运行测试，拒绝时会生成拒绝报告。"
        return result

    @app.post("/evals/run")
    def run_evals(config: EvalRunConfig) -> dict:
        return EvaluationHarness().run(config).model_dump()


def _get_task(task_id: str) -> TaskInstance:
    try:
        return TASK_RUNNER.get_task(task_id)
    except KeyError:
        if HTTPException is not None:
            raise HTTPException(status_code=404, detail="Task not found")
        raise KeyError(task_id)
