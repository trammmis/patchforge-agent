from __future__ import annotations

from app.schemas.base import BaseSchema, Field


class ReflectionRecord(BaseSchema):
    task_id: str
    task_type: str = "bugfix"
    success: bool
    reflection: str
    applicable_future_tasks: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class SkillRecord(BaseSchema):
    skill_id: str
    name: str
    trigger_conditions: list[str] = Field(default_factory=list)
    procedure: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)
    validated_on: list[str] = Field(default_factory=list)
    success_rate: float = 0.0
    version: str = "v1"


class FailurePattern(BaseSchema):
    failure_type: str
    symptoms: list[str] = Field(default_factory=list)
    likely_causes: list[str] = Field(default_factory=list)
    recommended_recovery: list[str] = Field(default_factory=list)

