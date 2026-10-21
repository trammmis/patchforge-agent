from __future__ import annotations

import hashlib
import os
import re
import shutil
from configparser import RawConfigParser
from pathlib import Path

from app.harness.sandbox_runner import SandboxRunner
from app.harness.trace_recorder import TraceRecorder
from app.schemas.task import TaskInstance
from app.schemas.test import CommandResult, EnvironmentSetupResult
from app.services.config import settings

VENV_DIR = ".patchforge_venv"
DEPENDENCY_MANIFESTS = (
    "requirements.txt",
    "test-requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
)
SCM_PRETEND_VERSION = "999.0.0"


class EnvironmentManager:
    """Prepare a task-local Python environment before the Agent starts editing."""

    def setup(
        self,
        task: TaskInstance,
        workspace: Path | str,
        sandbox: SandboxRunner,
        trace: TraceRecorder | None = None,
    ) -> EnvironmentSetupResult:
        workspace_path = Path(workspace)
        manifests = _detect_manifests(workspace_path)
        result = EnvironmentSetupResult(
            enabled=task.install_dependencies,
            manifests=manifests,
            docker_image=getattr(sandbox, "docker_image", None) if task.use_docker else None,
        )

        if not task.install_dependencies:
            result.status = "skipped"
            result.message = "未启用依赖准备；测试将直接使用当前运行环境。"
            _record(trace, result)
            return result
        if not manifests:
            result.status = "skipped"
            result.message = "未发现 requirements.txt、pyproject.toml、setup.py 或 setup.cfg。"
            _record(trace, result)
            return result

        test_extras = _detect_test_extras(workspace_path)
        cache_key = _environment_cache_key(workspace_path, manifests, sandbox)
        cache_enabled = bool(settings.environment_cache_enabled and task.use_docker)
        docker_fast_setup = bool(task.use_docker and settings.preconfigure_scm_version)
        result.cache_key = cache_key
        result.cache_path = str(settings.environment_cache_dir / cache_key / VENV_DIR)
        local_python = _local_venv_python(workspace_path, use_docker=task.use_docker)
        docker_python = f"/workspace/repo/{VENV_DIR}/bin/python"
        _configure_pip_environment(sandbox)

        if cache_enabled and _restore_environment_cache(
            workspace_path,
            settings.environment_cache_dir / cache_key / VENV_DIR,
            use_docker=task.use_docker,
        ):
            sandbox.configure_python_env(
                local_python,
                docker_python_executable=docker_python,
                path_prefix=local_python.parent,
            )
            result.status = "cached"
            result.cache_hit = True
            result.python_executable = str(local_python)
            result.docker_python_executable = docker_python
            result.message = "已复用依赖环境缓存，跳过虚拟环境创建和依赖安装。"
            _record(trace, result)
            return result

        if docker_fast_setup and not (workspace_path / ".git").exists():
            _configure_scm_fallback(sandbox)

        create_result = _run_setup_command(sandbox, "python -m venv .patchforge_venv", trace)
        result.commands.append(_command_summary(create_result))
        if create_result.returncode != 0:
            return _failed(result, trace, "virtualenv_create_failed", "创建任务虚拟环境失败。")

        sandbox.configure_python_env(
            local_python,
            docker_python_executable=docker_python,
            path_prefix=local_python.parent,
        )
        result.python_executable = str(local_python)
        result.docker_python_executable = docker_python

        commands: list[str] = []
        if task.allow_network and not (task.use_docker and settings.skip_pip_upgrade):
            commands.append("python -m pip install --upgrade pip")
        elif task.allow_network and task.use_docker and settings.skip_pip_upgrade:
            result.skipped.append("已跳过 pip 升级，直接使用基础镜像或虚拟环境内现有 pip。")
        elif "requirements.txt" in manifests:
            result.skipped.append("requirements.txt 需要允许网络后才会安装；当前仅准备虚拟环境并继续执行。")

        if "requirements.txt" in manifests and task.allow_network:
            commands.append("python -m pip install -r requirements.txt")
        elif "requirements.txt" in manifests:
            result.skipped.append(
                "requirements.txt 需要允许网络后才会安装；当前仅准备虚拟环境并继续执行。"
            )

        if "test-requirements.txt" in manifests and task.allow_network:
            commands.append("python -m pip install -r test-requirements.txt")
        elif "test-requirements.txt" in manifests:
            result.skipped.append(
                "test-requirements.txt 需要允许网络后才会安装；当前仅准备虚拟环境并继续执行。"
            )

        if any(name in manifests for name in ("pyproject.toml", "setup.py", "setup.cfg")):
            editable_command = "python -m pip install -e ."
            if test_extras:
                # Keep the requirement unquoted: command normalization preserves quote
                # characters for Docker's POSIX shell and pip would reject them.
                editable_command = f'python -m pip install -e .[{",".join(test_extras)}]'
            if not task.allow_network:
                editable_command += " --no-deps --no-build-isolation"
                result.skipped.append("未允许网络，项目安装使用 --no-deps --no-build-isolation。")
            commands.append(editable_command)

        for command in commands:
            command_result = _run_setup_command(sandbox, command, trace)
            result.commands.append(_command_summary(command_result))
            if command_result.returncode != 0 and command == "python -m pip install --upgrade pip":
                result.skipped.append("pip 升级失败，继续使用虚拟环境自带 pip 安装项目依赖。")
                continue
            if (
                command_result.returncode != 0
                and not docker_fast_setup
                and _should_retry_with_scm_version(command, command_result)
            ):
                _configure_scm_fallback(sandbox)
                result.skipped.append(
                    f"缺少 .git 元数据，已使用 SETUPTOOLS_SCM_PRETEND_VERSION={SCM_PRETEND_VERSION} 重试 editable install。"
                )
                command_result = _run_setup_command(sandbox, command, trace)
                result.commands.append(_command_summary(command_result))
            if command_result.returncode != 0:
                return _failed(result, trace, "dependency_install_failed", f"依赖准备命令失败：{command}")

        if cache_enabled:
            cache_venv = settings.environment_cache_dir / cache_key / VENV_DIR
            if _store_environment_cache(workspace_path / VENV_DIR, cache_venv):
                result.skipped.append("已保存依赖环境缓存，后续相同依赖任务可直接复用。")

        result.status = "partial" if result.skipped else "succeeded"
        result.message = "依赖准备完成。" if result.status == "succeeded" else "依赖准备部分完成，已记录跳过项。"
        _record(trace, result)
        return result


def _detect_manifests(workspace: Path) -> list[str]:
    return [name for name in DEPENDENCY_MANIFESTS if (workspace / name).exists()]


def _detect_test_extras(workspace: Path) -> list[str]:
    """Read tox's standard test extra so focused and full-suite runs share dependencies."""
    tox_path = workspace / "tox.ini"
    if not tox_path.exists():
        return []
    try:
        parser = RawConfigParser()
        parser.read(tox_path, encoding="utf-8")
        raw_extras = parser.get("testenv", "extras", fallback="")
    except (OSError, ValueError):
        return []
    extras: list[str] = []
    for value in re.split(r"[\s,]+", raw_extras):
        normalized = value.strip()
        if normalized and normalized not in extras:
            extras.append(normalized)
    return extras


def _local_venv_python(workspace: Path, *, use_docker: bool = False) -> Path:
    scripts_dir = "bin" if use_docker else ("Scripts" if os.name == "nt" else "bin")
    executable = "python.exe" if os.name == "nt" else "python"
    return workspace / VENV_DIR / scripts_dir / executable


def _environment_cache_key(workspace: Path, manifests: list[str], sandbox: SandboxRunner) -> str:
    digest = hashlib.sha256()
    digest.update(b"patchforge-env-v2\n")
    digest.update(str(getattr(sandbox, "docker_image", "local")).encode("utf-8"))
    digest.update(f"\n{os.name}\n".encode("utf-8"))
    for name in manifests:
        digest.update(name.encode("utf-8"))
        digest.update(b"\n")
        digest.update((workspace / name).read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()[:24]


def _restore_environment_cache(workspace: Path, cache_venv: Path, *, use_docker: bool) -> bool:
    marker = cache_venv / ".patchforge_complete"
    executable = cache_venv / (
        "bin/python" if use_docker else ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not marker.exists() or not executable.exists():
        return False
    target = workspace / VENV_DIR
    try:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(cache_venv, target)
    except OSError:
        return False
    return True


def _store_environment_cache(source_venv: Path, cache_venv: Path) -> bool:
    try:
        if not source_venv.exists():
            return False
        if not any((source_venv / relative).exists() for relative in ("bin/python", "Scripts/python.exe")):
            return False
    except OSError:
        # Docker 在 Windows 挂载目录中生成的 Linux 符号链接，可能无法被宿主机解析。
        # 这种情况下跳过缓存保存，不应影响任务本身的执行结果。
        return False
    temporary = cache_venv.with_name(f"{cache_venv.name}.tmp-{os.getpid()}")
    try:
        cache_venv.parent.mkdir(parents=True, exist_ok=True)
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source_venv, temporary)
        (temporary / ".patchforge_complete").write_text("ok\n", encoding="utf-8")
        if not cache_venv.exists():
            temporary.replace(cache_venv)
        else:
            shutil.rmtree(temporary)
    except OSError:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        return False
    return True


def _run_setup_command(sandbox: SandboxRunner, command: str, trace: TraceRecorder | None) -> CommandResult:
    result = sandbox.run(command, timeout=300)
    if trace:
        trace.record("environment_setup_command", _command_summary(result))
    return result


def _should_retry_with_scm_version(command: str, result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return (
        "pip install -e" in command
        and "setuptools-scm was unable to detect version" in output
    )


def _configure_scm_fallback(sandbox: SandboxRunner) -> None:
    if hasattr(sandbox, "configure_environment"):
        sandbox.configure_environment(
            {
                "SETUPTOOLS_SCM_PRETEND_VERSION": SCM_PRETEND_VERSION,
                "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST": SCM_PRETEND_VERSION,
            }
        )


def _configure_pip_environment(sandbox: SandboxRunner) -> None:
    if hasattr(sandbox, "configure_environment"):
        sandbox.configure_environment(
            {
                "PIP_DEFAULT_TIMEOUT": "120",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_PROGRESS_BAR": "off",
                "PIP_RETRIES": "10",
            }
        )


def _command_summary(result: CommandResult) -> dict:
    return {
        "command": result.command,
        "executed_command": result.executed_command,
        "returncode": result.returncode,
        "blocked": result.blocked,
        "timed_out": result.timed_out,
        "duration_ms": result.duration_ms,
        "message": result.message,
        "stdout_excerpt": result.stdout[-1200:],
        "stderr_excerpt": result.stderr[-1200:],
    }


def _failed(
    result: EnvironmentSetupResult,
    trace: TraceRecorder | None,
    failure_type: str,
    message: str,
) -> EnvironmentSetupResult:
    result.status = "failed"
    result.failure_type = failure_type
    result.message = message
    _record(trace, result)
    return result


def _record(trace: TraceRecorder | None, result: EnvironmentSetupResult) -> None:
    if trace:
        trace.record("environment_setup", result.model_dump())
