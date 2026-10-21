from __future__ import annotations

from typing import Any

from app.schemas.base import BaseSchema, Field
from app.schemas.task import TaskRunResult


class TimelineItem(BaseSchema):
    index: int
    timestamp: float | None = None
    elapsed_seconds: float | None = None
    event_type: str
    category: str
    title: str
    status: str = "completed"
    detail: str = ""
    phase: str | None = None
    tool_name: str | None = None
    duration_ms: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TimelineResult(BaseSchema):
    task_id: str
    trace_path: str | None = None
    latest_phase: str = "created"
    progress: float = 0.0
    terminal: bool = False
    items: list[TimelineItem] = Field(default_factory=list)


class TaskRunStatus(BaseSchema):
    task_id: str
    status: str
    current_phase: str = "created"
    iteration_count: int = 0
    queued_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    duration_seconds: float | None = None
    failure_type: str | None = None
    error_message: str | None = None
    report_path: str | None = None
    trace_path: str | None = None
    result: TaskRunResult | None = None
