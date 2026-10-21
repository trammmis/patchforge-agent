from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.schemas.run import TaskRunStatus
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.database import connect
from app.services.config import settings


class TaskStore:
    """SQLite-backed task registry for API/task-runner state."""

    def __init__(self, db_path: Path | str | None = None, runs_dir: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else settings.data_dir / "patchforge.sqlite3"
        self.runs_dir = Path(runs_dir) if runs_dir is not None else settings.runs_dir

    def create_task(self, task: TaskInstance) -> TaskRunStatus:
        now = _now()
        raw_issue = f"{task.issue_title}\n{task.issue_body}".strip()
        with self._connect() as conn:
            existing = conn.execute("SELECT created_at FROM tasks WHERE id = ?", (task.task_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE tasks
                    SET repo_url = ?, repo_path = ?, issue_url = ?, raw_issue = ?, task_payload = ?,
                        result_payload = NULL, status = ?, current_phase = ?, iteration_count = 0,
                        failure_type = NULL, error_message = NULL, report_path = NULL, trace_path = ?,
                        queued_at = NULL, started_at = NULL, finished_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        task.repo_url,
                        task.repo_path,
                        task.issue_url,
                        raw_issue,
                        task.model_dump_json(),
                        "created",
                        "created",
                        str(self.trace_path(task.task_id)),
                        now,
                        task.task_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, repo_url, repo_path, issue_url, raw_issue, task_payload, result_payload,
                        status, current_phase, iteration_count, failure_type, error_message,
                        report_path, trace_path, queued_at, started_at, finished_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL, NULL, NULL, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.repo_url,
                        task.repo_path,
                        task.issue_url,
                        raw_issue,
                        task.model_dump_json(),
                        "created",
                        "created",
                        str(self.trace_path(task.task_id)),
                        now,
                        now,
                    ),
                )
        return self.get_status(task.task_id)

    def mark_queued(self, task_id: str, queued_at: float) -> None:
        self._update(
            task_id,
            status="queued",
            current_phase="created",
            iteration_count=0,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            failure_type=None,
            error_message=None,
            report_path=None,
            trace_path=str(self.trace_path(task_id)),
            result_payload=None,
        )

    def mark_running(self, task_id: str, started_at: float) -> None:
        self._update(task_id, status="running", current_phase="created", started_at=started_at)

    def mark_finished(self, task_id: str, finished_at: float, result: TaskRunResult) -> None:
        self._update(
            task_id,
            status=result.status,
            current_phase="finished",
            iteration_count=result.iterations,
            finished_at=finished_at,
            failure_type=result.failure_type,
            error_message=result.error_message,
            report_path=result.report_path,
            trace_path=result.trace_path,
            result_payload=result.model_dump_json(),
        )

    def mark_failed(
        self,
        task_id: str,
        finished_at: float,
        *,
        failure_type: str,
        error_message: str,
        current_phase: str = "failed",
    ) -> None:
        self._update(
            task_id,
            status="failed",
            current_phase=current_phase,
            finished_at=finished_at,
            failure_type=failure_type,
            error_message=error_message,
        )

    def mark_interrupted_runs(self) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    current_phase = 'interrupted',
                    failure_type = 'runner_interrupted',
                    error_message = '服务重启或进程退出，中断了未完成任务。',
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (_epoch(), now),
            )

    def get_task(self, task_id: str) -> TaskInstance:
        row = self._row(task_id)
        payload = row["task_payload"]
        if not payload:
            raise KeyError(task_id)
        return TaskInstance.model_validate(json.loads(payload))

    def get_status(self, task_id: str) -> TaskRunStatus:
        return self._status_from_row(self._row(task_id))

    def list_statuses(self, limit: int = 50) -> list[TaskRunStatus]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._status_from_row(row) for row in rows]

    def trace_path(self, task_id: str) -> Path:
        return self.runs_dir / task_id / "trace.jsonl"

    def _row(self, task_id: str):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row

    def _update(self, task_id: str, **values) -> None:
        values["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [values[key] for key in values]
        params.append(task_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", params)

    def _status_from_row(self, row) -> TaskRunStatus:
        result = _load_result(row["result_payload"])
        return TaskRunStatus(
            task_id=row["id"],
            status=row["status"] or "created",
            current_phase=row["current_phase"] or "created",
            iteration_count=int(row["iteration_count"] or 0),
            queued_at=row["queued_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            duration_seconds=_duration(row["started_at"], row["finished_at"]),
            failure_type=row["failure_type"],
            error_message=row["error_message"],
            report_path=row["report_path"],
            trace_path=row["trace_path"] or str(self.trace_path(row["id"])),
            result=result,
        )

    @contextmanager
    def _connect(self) -> Iterator:
        conn = connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _load_result(payload: str | None) -> TaskRunResult | None:
    if not payload:
        return None
    return TaskRunResult.model_validate(json.loads(payload))


def _duration(started_at: float | None, finished_at: float | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return round(float(finished_at) - float(started_at), 3)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch() -> float:
    return datetime.now(timezone.utc).timestamp()
