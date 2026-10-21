from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: Path | str = ".env") -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real env vars."""

    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Runtime settings with environment-variable overrides."""

    def __init__(self) -> None:
        load_local_env()
        self.project_root = Path.cwd()
        self.data_dir = Path(os.getenv("PATCHFORGE_DATA_DIR", ".patchforge_data"))
        self.runs_dir = Path(os.getenv("PATCHFORGE_RUNS_DIR", ".patchforge_runs"))
        self.environment_cache_dir = Path(
            os.getenv("PATCHFORGE_ENV_CACHE_DIR", str(self.data_dir / "env_cache"))
        )
        # Docker tasks are isolated by workspace, so reusing an immutable dependency
        # environment is safe and avoids rebuilding the same venv for every eval case.
        self.environment_cache_enabled = _env_bool("PATCHFORGE_ENV_CACHE_ENABLED", True)
        # The base image already provides a usable pip; upgrading it adds network
        # latency and creates an avoidable failure point for offline or rate-limited runs.
        self.skip_pip_upgrade = _env_bool("PATCHFORGE_SKIP_PIP_UPGRADE", True)
        # GitHub archive downloads do not contain .git metadata, so preconfigure the
        # package version before editable installation when Docker is used.
        self.preconfigure_scm_version = _env_bool("PATCHFORGE_PRECONFIGURE_SCM_VERSION", True)
        self.model = os.getenv("PATCHFORGE_MODEL", "offline-rule-agent")
        self.use_docker = os.getenv("PATCHFORGE_USE_DOCKER", "false").lower() == "true"
        self.docker_image = os.getenv("PATCHFORGE_DOCKER_IMAGE", "python:3.12-slim")
        self.llm_provider = os.getenv("PATCHFORGE_LLM_PROVIDER", "offline").lower()
        self.llm_rule_fallback = os.getenv("PATCHFORGE_LLM_RULE_FALLBACK", "true").lower() == "true"
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.deepseek_fallback_model = os.getenv("DEEPSEEK_FALLBACK_MODEL", "deepseek-chat").strip()
        self.deepseek_temperature = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.1"))
        self.deepseek_max_tokens = int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192"))
        self.deepseek_empty_retry_max_tokens = int(os.getenv("DEEPSEEK_EMPTY_RETRY_MAX_TOKENS", "12288"))
        self.deepseek_timeout_seconds = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "90"))
        self.deepseek_request_retries = int(os.getenv("DEEPSEEK_REQUEST_RETRIES", "2"))
        self.deepseek_retry_backoff_seconds = float(os.getenv("DEEPSEEK_RETRY_BACKOFF_SECONDS", "1.0"))
        self.deepseek_fast_repair = _env_bool("PATCHFORGE_LLM_FAST_REPAIR", False)
        self.evolution_retrieval = os.getenv("PATCHFORGE_EVOLUTION_RETRIEVAL", "semantic").lower()
        self.github_token = os.getenv("PATCHFORGE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN", "")

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider == "deepseek" and bool(self.deepseek_api_key)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
