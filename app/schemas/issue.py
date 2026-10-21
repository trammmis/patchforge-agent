from __future__ import annotations

from app.schemas.base import BaseSchema, Field


class ParsedIssue(BaseSchema):
    task_type: str = "bugfix"
    summary: str
    expected_behavior: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    suspected_area: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    assumptions: list[str] = Field(default_factory=list)

