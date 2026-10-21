from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import time
from pathlib import Path

from app.schemas.test import CommandResult

ALLOWED_COMMANDS = {
    "python",
    "pytest",
    "ruff",
    "mypy",
    "npm",
    "eslint",
    "tsc",
    "rg",
    "grep",
    "ls",
    "cat",
    "sed",
}
FORBIDDEN_FRAGMENTS = [
    "sudo",
    "rm -rf /",
    "curl",
    "wget",
    "ssh",
    "scp",
    "chmod 777",
    "mkfs",
    " dd ",
    "docker",
    "kubectl",
    "&&",
    "||",
    ";",
]


def is_command_allowed(command: str) -> tuple[bool, str]:
    lowered = f" {command.lower()} "
    for fragment in FORBIDDEN_FRAGMENTS:
        if fragment in lowered:
            return False, f"Forbidden command fragment: {fragment.strip()}"
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return False, str(exc)
    if not parts:
        return False, "Empty command"
    executable = Path(parts[0]).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable not in ALLOWED_COMMANDS:
        return False, f"Command not allowed: {parts[0]}"
    if executable == "npm" and len(parts) > 1 and parts[1] not in {"test", "run"}:
        return False, "Only npm test/npm run are allowed"
    return True, ""


def normalize_test_command(
    command: str,
    python_executable: str | None = None,
    *,
    posix_shell: bool = False,
) -> str:
    return _format_command_parts(
        normalize_command_parts(command, python_executable=python_executable),
        posix_shell=posix_shell,
    )


def normalize_command_parts(command: str, python_executable: str | None = None) -> list[str]:
    parts = shlex.split(command, posix=False)
    if not parts:
        return []
    executable = _normalized_executable(parts[0])
    is_python = executable == "python"
    is_pytest = executable == "pytest" or (
        len(parts) >= 3 and is_python and parts[1] == "-m" and parts[2] == "pytest"
    )
    if python_executable:
        if is_python:
            return [python_executable, *parts[1:]]
        if executable == "pytest":
            return [python_executable, "-m", "pytest", *parts[1:]]
        if executable in {"ruff", "mypy"}:
            return [python_executable, "-m", executable, *parts[1:]]
    if is_pytest and importlib.util.find_spec("pytest") is None:
        return ["python", "-m", "unittest", "discover", "-s", "tests"]
    return parts


def run_command(
    workspace: Path | str,
    command: str,
    timeout: int = 60,
    python_executable: str | None = None,
    path_prefix: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> CommandResult:
    allowed, reason = is_command_allowed(command)
    if not allowed:
        return CommandResult(
            command=command,
            executed_command=command,
            returncode=126,
            blocked=True,
            message=reason,
            stderr=reason,
        )
    command_parts = normalize_command_parts(command, python_executable=python_executable)
    executed_command = _format_command_parts(command_parts)
    started = time.perf_counter()
    try:
        env = os.environ.copy()
        if path_prefix:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            command_parts,
            cwd=Path(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CommandResult(
            command=command,
            executed_command=executed_command,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
            message="ok" if proc.returncode == 0 else "command failed",
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CommandResult(
            command=command,
            executed_command=executed_command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_ms=duration_ms,
            timed_out=True,
            message="command timed out",
        )


def _normalized_executable(raw: str) -> str:
    executable = Path(raw).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return executable


def _format_command_parts(parts: list[str], *, posix_shell: bool = False) -> str:
    if not parts:
        return ""
    if posix_shell:
        return " ".join(shlex.quote(part) for part in parts)
    return subprocess.list2cmdline(parts)
