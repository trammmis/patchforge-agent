from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None

from app.api import tasks as task_api
from app.main import create_app
from app.schemas.run import TaskRunStatus, TimelineItem, TimelineResult
from app.schemas.task import TaskInstance


class FakeTaskRunner:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskInstance] = {}

    def register(self, task: TaskInstance) -> TaskRunStatus:
        self.tasks[task.task_id] = task
        return TaskRunStatus(task_id=task.task_id, status="created")

    def get_task(self, task_id: str) -> TaskInstance:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return self.tasks[task_id]

    def start(self, task_id: str) -> TaskRunStatus:
        self.get_task(task_id)
        return TaskRunStatus(task_id=task_id, status="queued", current_phase="created")

    def get_status(self, task_id: str) -> TaskRunStatus:
        self.get_task(task_id)
        return TaskRunStatus(task_id=task_id, status="running", current_phase="inspect_repo")

    def get_events(self, task_id: str) -> list[dict]:
        self.get_task(task_id)
        return [{"type": "agent_phase", "payload": {"phase": "inspect_repo"}}]

    def get_timeline(self, task_id: str) -> TimelineResult:
        self.get_task(task_id)
        return TimelineResult(
            task_id=task_id,
            latest_phase="inspect_repo",
            progress=0.2,
            items=[
                TimelineItem(
                    index=1,
                    event_type="agent_phase",
                    category="agent",
                    title="探索仓库",
                    phase="inspect_repo",
                )
            ],
        )

    def get_result(self, task_id: str):
        self.get_task(task_id)
        return None

    def list_statuses(self, limit: int = 50) -> list[TaskRunStatus]:
        return [TaskRunStatus(task_id=task_id, status="created") for task_id in list(self.tasks)[:limit]]

    def approve(self, task_id: str, approved: bool, reviewer: str = "human", notes: str = "") -> TaskRunStatus:
        self.get_task(task_id)
        return TaskRunStatus(task_id=task_id, status="succeeded" if approved else "failed", current_phase="finished")


@unittest.skipIf(TestClient is None, "FastAPI test client is not installed")
class APITasksTests(unittest.TestCase):
    def test_api_starts_task_in_background_and_exposes_timeline(self) -> None:
        old_runner = task_api.TASK_RUNNER
        task_api.TASK_RUNNER = FakeTaskRunner()  # type: ignore[assignment]
        try:
            client = TestClient(create_app())
            created = client.post(
                "/tasks",
                json={
                    "repo_path": "examples/demo_python_repo",
                    "issue_title": "Fix bug",
                    "test_command": "pytest",
                },
            )
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task_id"]

            started = client.post(f"/tasks/{task_id}/run")
            status = client.get(f"/tasks/{task_id}")
            timeline = client.get(f"/tasks/{task_id}/timeline")
            listing = client.get("/tasks")
            approval = client.post(
                f"/tasks/{task_id}/approve-pr",
                json={"approved": True, "reviewer": "tester", "notes": "ok"},
            )

            self.assertEqual(started.json()["status"], "queued")
            self.assertEqual(status.json()["current_phase"], "inspect_repo")
            self.assertEqual(timeline.json()["items"][0]["title"], "探索仓库")
            self.assertEqual(listing.json()[0]["task_id"], task_id)
            self.assertEqual(approval.json()["status"], "succeeded")
            self.assertTrue(approval.json()["approved"])
        finally:
            task_api.TASK_RUNNER = old_runner


if __name__ == "__main__":
    unittest.main()
