from __future__ import annotations

from pathlib import Path


class PathPolicyError(ValueError):
    """Raised when a path escapes the harness-managed workspace."""


def safe_join(root: Path | str, path: str | Path = ".") -> Path:
    root_path = Path(root).resolve()
    candidate = (root_path / path).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise PathPolicyError(f"Path escapes workspace: {path}") from exc
    return candidate


def display_path(root: Path | str, path: Path | str) -> str:
    return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")

