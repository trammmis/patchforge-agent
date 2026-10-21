from __future__ import annotations

from typing import Any

from app.schemas.base import BaseSchema, Field


class CommandResult(BaseSchema):
    command: str
    executed_command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    blocked: bool = False
    message: str = ""


class EnvironmentSetupResult(BaseSchema):
    status: str = "skipped"
    enabled: bool = False
    manifests: list[str] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    python_executable: str | None = None
    docker_python_executable: str | None = None
    docker_image: str | None = None
    cache_key: str | None = None
    cache_hit: bool = False
    cache_path: str | None = None
    message: str = ""
    failure_type: str | None = None


class ParsedTestResult(BaseSchema):
    status: str
    failed_tests: list[str] = Field(default_factory=list)
    error_summary: str = ""
    failure_type: str | None = None


class TestResult(BaseSchema):
    status: str
    command: str
    executed_command: str
    failed_tests: list[str] = Field(default_factory=list)
    error_summary: str = ""
    raw_output: str = ""
    duration_ms: int = 0
    failure_type: str | None = None
