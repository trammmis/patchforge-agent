from __future__ import annotations

from app.schemas.base import BaseSchema, Field


class PatchResult(BaseSchema):
    success: bool
    message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    risk_reasons: list[str] = Field(default_factory=list)


class EditResult(BaseSchema):
    success: bool
    message: str = ""
    file_path: str | None = None
    diff: str | None = None
    requires_approval: bool = False
    risk_reasons: list[str] = Field(default_factory=list)

