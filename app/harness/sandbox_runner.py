from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from pathlib import Path

from app.schemas.test import CommandResult
from app.services.config import settings
from app.tools.shell_tools import is_command_allowed, normalize_test_command, run_command


class SandboxRunner:
    def __init__(
        self,
        workspace: Path | str,
        use_docker: bool = False,
        allow_network: bool = False,
        docker_image: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.use_docker = use_docker
        self.allow_network = allow_network
        self.docker_image = docker_image or settings.docker_image
        self.python_executable: Path | None = None
        self.docker_python_executable: str | None = None
        self.path_prefix: Path | None = None
        self.extra_env: dict[str, str] = {}

    def configure_python_env(
        self,
        python_executable: Path | str,
        *,
        docker_python_executable: str | None = None,
        path_prefix: Path | str | None = None,
    ) -> None:
        self.python_executable = Path(python_executable)
        self.docker_python_executable = docker_python_executable
        self.path_prefix = Path(path_prefix) if path_prefix is not None else self.python_executable.parent

    def configure_environment(self, env: dict[str, str]) -> None:
        self.extra_env.update(env)

    def run(self, command: str, timeout: int = 60) -> CommandResult:
        if not self.use_docker:
            return run_command(
                self.workspace,
                command,
                timeout=timeout,
                python_executable=str(self.python_executable) if self.python_executable else None,
                path_prefix=str(self.path_prefix) if self.path_prefix else None,
                extra_env=self.extra_env,
            )
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
        if shutil.which("docker") is None:
            return CommandResult(
                command=command,
                executed_command=command,
                returncode=127,
                blocked=True,
                message="Docker not found",
                stderr="Docker not found",
            )
        executed = normalize_test_command(
            command,
            python_executable=self.docker_python_executable,
            posix_shell=True,
        )
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--memory=2g",
            "--cpus=2",
            "--network",
            "bridge" if self.allow_network else "none",
        ]
        for key, value in sorted(self.extra_env.items()):
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend(
            [
                "-v",
                f"{self.workspace.as_posix()}:/workspace/repo",
                "-w",
                "/workspace/repo",
                self.docker_image,
                "sh",
                "-lc",
                executed,
            ]
        )
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return CommandResult(
                command=command,
                executed_command=" ".join(shlex.quote(part) for part in docker_command),
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=int((time.perf_counter() - started) * 1000),
                message="ok" if proc.returncode == 0 else "docker command failed",
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                executed_command=" ".join(shlex.quote(part) for part in docker_command),
                returncode=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=True,
                message="docker command timed out",
            )
