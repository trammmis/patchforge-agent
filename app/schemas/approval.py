from __future__ import annotations

from app.schemas.base import BaseSchema, Field


class ApprovalRequest(BaseSchema):
    task_id: str
    reason: str
    diff: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    approved: bool = False


class ApprovalResult(BaseSchema):
    task_id: str
    approved: bool
    reviewer: str = "human"
    notes: str = ""

