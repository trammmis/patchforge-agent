from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.harness.execution_harness import ExecutionHarness
from app.harness.timeline import build_timeline, read_trace_events
from app.schemas.run import TaskRunStatus, TimelineResult
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.config import settings
from app.storage.task_store import TaskStore


@dataclass
class _TaskRecord:
    task: TaskInstance
    status: str = "created"
    queued_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    result: TaskRunResult | None = None
    failure_type: str | None = None
    error_message: str | None = None
    future: Future | None = None


class TaskRunner:
    """Small in-process runner for API demos and local control-plane testing."""

    def __init__(
        self,
        harness_factory: Callable[[], ExecutionHarness] = ExecutionHarness,
        runs_dir: Path | str | None = None,
        task_store: TaskStore | None = None,
        max_workers: int = 2,
    ) -> None:
        self.harness_factory = harness_factory
        self.runs_dir = Path(runs_dir) if runs_dir is not None else settings.runs_dir
        self.store = task_store or TaskStore(runs_dir=self.runs_dir)
        self.store.mark_interrupted_runs()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="patchforge-task")
        self._records: dict[str, _TaskRecord] = {}
        self._lock = threading.RLock()

    def register(self, task: TaskInstance) -> TaskRunStatus:
        with self._lock:
            self._records[task.task_id] = _TaskRecord(task=task)
            self.store.create_task(task)
            return self._status_for_record(task.task_id, self._records[task.task_id])

    def get_task(self, task_id: str) -> TaskInstance:
        with self._lock:
            if task_id in self._records:
                return self._records[task_id].task
        return self.store.get_task(task_id)

    def start(self, task_id: str) -> TaskRunStatus:
        with self._lock:
            if task_id not in self._records:
                persisted = self.store.get_status(task_id)
                self._records[task_id] = _TaskRecord(
                    task=self.store.get_task(task_id),
                    status=persisted.status,
                    queued_at=persisted.queued_at,
                    started_at=persisted.started_at,
                    finished_at=persisted.finished_at,
                    result=persisted.result,
                    failure_type=persisted.failure_type,
                    error_message=persisted.error_message,
                )
            record = self._records[task_id]
            if record.status in {"queued", "running"}:
                return self._status_for_record(task_id, record)
            record.status = "queued"
            record.queued_at = time.time()
            record.started_at = None
            record.finished_at = None
            record.result = None
            record.failure_type = None
            record.error_message = None
            self.store.mark_queued(task_id, record.queued_at)
            record.future = self.executor.submit(self._run_task, task_id)
            return self._status_for_record(task_id, record)

    def get_status(self, task_id: str) -> TaskRunStatus:
        with self._lock:
            if task_id in self._records:
                return self._status_for_record(task_id, self._records[task_id])
        return self.store.get_status(task_id)

    def list_statuses(self, limit: int = 50) -> list[TaskRunStatus]:
        persisted = {status.task_id: status for status in self.store.list_statuses(limit=limit)}
        with self._lock:
            for task_id, record in self._records.items():
                persisted[task_id] = self._status_for_record(task_id, record)
        return sorted(
            persisted.values(),
            key=lambda item: item.finished_at or item.started_at or item.queued_at or 0,
            reverse=True,
        )[:limit]

    def get_events(self, task_id: str) -> list[dict]:
        self.get_task(task_id)
        return read_trace_events(self._trace_path(task_id))

    def get_timeline(self, task_id: str) -> TimelineResult:
        self.get_task(task_id)
        return build_timeline(task_id, self._trace_path(task_id))

    def get_result(self, task_id: str) -> TaskRunResult | None:
        with self._lock:
            if task_id in self._records:
                return self._records[task_id].result
        return self.store.get_status(task_id).result

    def approve(self, task_id: str, approved: bool, reviewer: str = "human", notes: str = "") -> TaskRunStatus:
        with self._lock:
            task = self.get_task(task_id)
            current = self.get_status(task_id)
            previous_result = current.result
            if previous_result is None:
                raise ValueError("Task has no result to approve.")
            record = self._records.get(
                task_id,
                _TaskRecord(
                    task=task,
                    status=current.status,
                    queued_at=current.queued_at,
                    started_at=current.started_at,
                    finished_at=current.finished_at,
                    result=previous_result,
                    failure_type=current.failure_type,
                    error_message=current.error_message,
                ),
            )
            record.status = "running"
            record.started_at = time.time()
            record.finished_at = None
            self._records[task_id] = record
            self.store.mark_running(task_id, record.started_at)
        try:
            result = self.harness_factory().resume_after_approval(
                task,
                previous_result,
                approved=approved,
                reviewer=reviewer,
                notes=notes,
            )
            with self._lock:
                record.result = result
                record.status = result.status
                record.failure_type = result.failure_type
                record.error_message = result.error_message
                record.finished_at = time.time()
                self.store.mark_finished(task_id, record.finished_at, result)
                return self._status_for_record(task_id, record)
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.failure_type = "approval_resume_error"
                record.error_message = str(exc)
                record.finished_at = time.time()
                self.store.mark_failed(
                    task_id,
                    record.finished_at,
                    failure_type=record.failure_type,
                    error_message=record.error_message,
                )
                return self._status_for_record(task_id, record)

    def wait(self, task_id: str, timeout: float | None = None) -> TaskRunStatus:
        with self._lock:
            if task_id not in self._records:
                raise KeyError(task_id)
            future = self._records[task_id].future
        if future is not None:
            future.result(timeout=timeout)
        return self.get_status(task_id)

    def close(self) -> None:
        self.executor.shutdown(wait=True)

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            record = self._records[task_id]
            record.status = "running"
            record.started_at = time.time()
            self.store.mark_running(task_id, record.started_at)
        try:
            result = self.harness_factory().run_task(record.task)
            with self._lock:
                record.result = result
                record.status = result.status
                record.failure_type = result.failure_type
                record.error_message = result.error_message
                record.finished_at = time.time()
                self.store.mark_finished(task_id, record.finished_at, result)
        except Exception as exc:  # pragma: no cover - defensive boundary for API background runs.
            with self._lock:
                record.status = "failed"
                record.failure_type = "unhandled_runner_error"
                record.error_message = str(exc)
                record.finished_at = time.time()
                self.store.mark_failed(
                    task_id,
                    record.finished_at,
                    failure_type=record.failure_type,
                    error_message=record.error_message,
                )

    def _status_for_record(self, task_id: str, record: _TaskRecord) -> TaskRunStatus:
        timeline = build_timeline(task_id, self._trace_path(task_id))
        result = record.result
        started_at = record.started_at
        finished_at = record.finished_at
        duration = None
        if started_at and finished_at:
            duration = round(finished_at - started_at, 3)
        elif started_at and record.status == "running":
            duration = round(time.time() - started_at, 3)
        return TaskRunStatus(
            task_id=task_id,
            status=record.status,
            current_phase="finished" if result else timeline.latest_phase,
            iteration_count=result.iterations if result else _iteration_count(timeline.items),
            queued_at=record.queued_at,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            failure_type=(result.failure_type if result else record.failure_type),
            error_message=(result.error_message if result else record.error_message),
            report_path=result.report_path if result else None,
            trace_path=result.trace_path if result else str(self._trace_path(task_id)),
            result=result,
        )

    def _trace_path(self, task_id: str) -> Path:
        return self.runs_dir / task_id / "trace.jsonl"


def _iteration_count(items: list) -> int:
    return sum(1 for item in items if item.phase == "generate_patch")
