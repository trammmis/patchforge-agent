from __future__ import annotations

import difflib
import re
from pathlib import Path

from app.schemas.patch import EditResult, PatchResult
from app.tools.path_guard import display_path, safe_join

DIFF_HEADER = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")
HIGH_RISK_FILES = {"pyproject.toml", "requirements.txt", "poetry.lock", "Dockerfile", "docker-compose.yml"}


def build_unified_diff(path: str, old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff) + "\n"


def changed_files_from_diff(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
    return files


def risk_reasons_for_files(files: list[str], max_files: int = 5) -> list[str]:
    reasons = []
    if len(files) > max_files:
        reasons.append(f"modifies {len(files)} files, above limit {max_files}")
    for file in files:
        name = Path(file).name
        if name in HIGH_RISK_FILES or ".github/" in file:
            reasons.append(f"high-risk file modified: {file}")
    return reasons


def apply_unified_diff(root: Path | str, diff: str, check: bool = False) -> PatchResult:
    files = changed_files_from_diff(diff)
    if not files:
        return PatchResult(success=False, message="No changed files found in diff")
    risk_reasons = risk_reasons_for_files(files)
    try:
        new_contents = _patched_contents(root, diff)
    except Exception as exc:
        return PatchResult(success=False, message=str(exc), changed_files=files)
    if not check:
        for file_path, content in new_contents.items():
            target = safe_join(root, file_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return PatchResult(
        success=True,
        message="Patch checked" if check else "Patch applied",
        changed_files=files,
        requires_approval=bool(risk_reasons),
        risk_reasons=risk_reasons,
    )


def check_python_syntax_after_patch(root: Path | str, diff: str) -> dict[str, object] | None:
    """Return a syntax diagnostic if patched Python files would not compile."""

    try:
        new_contents = _patched_contents(root, diff)
    except Exception as exc:
        return {"success": False, "message": str(exc)}
    for file_path, content in new_contents.items():
        if not file_path.endswith(".py"):
            continue
        try:
            compile(content, file_path, "exec")
        except SyntaxError as exc:
            location = f"{file_path}:{exc.lineno or '?'}:{exc.offset or '?'}"
            return {
                "success": False,
                "file": file_path,
                "location": location,
                "message": f"{exc.__class__.__name__}: {exc.msg} at {location}",
                "text": (exc.text or "").strip(),
            }
    return None


def replace_range(
    root: Path | str,
    file_path: str,
    start_line: int,
    end_line: int,
    new_content: str,
) -> EditResult:
    target = safe_join(root, file_path)
    old_text = target.read_text(encoding="utf-8")
    lines = old_text.splitlines()
    start = max(start_line, 1) - 1
    end = max(end_line, start_line) if end_line else start_line
    replacement = new_content.splitlines()
    updated_lines = lines[:start] + replacement + lines[end:]
    updated = "\n".join(updated_lines) + ("\n" if old_text.endswith("\n") else "")
    diff = build_unified_diff(display_path(root, target), old_text, updated)
    result = apply_unified_diff(root, diff)
    return EditResult(
        success=result.success,
        message=result.message,
        file_path=file_path,
        diff=diff,
        requires_approval=result.requires_approval,
        risk_reasons=result.risk_reasons,
    )


def _patched_contents(root: Path | str, diff: str) -> dict[str, str]:
    lines = diff.splitlines()
    idx = 0
    patched: dict[str, str] = {}
    while idx < len(lines):
        if not lines[idx].startswith("--- "):
            idx += 1
            continue
        old_header = lines[idx]
        idx += 1
        if idx >= len(lines) or not lines[idx].startswith("+++ "):
            raise ValueError(f"Malformed diff after {old_header}")
        new_header = lines[idx]
        idx += 1
        if not new_header.startswith("+++ b/"):
            raise ValueError(f"Only b/ file targets are supported: {new_header}")
        file_path = new_header[6:]
        target = safe_join(root, file_path)
        original = target.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        cursor = 0
        saw_hunk = False
        while idx < len(lines) and not _is_file_boundary(lines[idx]):
            if not lines[idx].startswith("@@ "):
                idx += 1
                continue
            match = DIFF_HEADER.match(lines[idx])
            if not match:
                raise ValueError(f"Malformed diff hunk header: {lines[idx]!r}")
            saw_hunk = True
            old_start = int(match.group("old_start"))
            idx += 1
            hunk_lines: list[str] = []
            while idx < len(lines) and not lines[idx].startswith("@@ ") and not _is_file_boundary(lines[idx]):
                line = lines[idx]
                idx += 1
                if line.startswith("\\"):
                    continue
                hunk_lines.append(line)
            hunk_start = _locate_hunk(original, hunk_lines, max(old_start - 1, cursor), cursor)
            output.extend(original[cursor:hunk_start])
            cursor = _apply_hunk(original, hunk_lines, hunk_start, output, file_path)
        if not saw_hunk:
            raise ValueError(f"No valid hunks found for {file_path}")
        output.extend(original[cursor:])
        patched[file_path] = "\n".join(output) + "\n"
    return patched


def _is_file_boundary(line: str) -> bool:
    return line.startswith("--- ") or line.startswith("diff --git ")


def _old_sequence(hunk_lines: list[str]) -> list[str]:
    return [line[1:] for line in hunk_lines if line[:1] in {" ", "-"}]


def _sequence_matches(original: list[str], start: int, sequence: list[str]) -> bool:
    if start < 0 or start + len(sequence) > len(original):
        return False
    return original[start : start + len(sequence)] == sequence


def _locate_hunk(original: list[str], hunk_lines: list[str], preferred_start: int, min_start: int) -> int:
    sequence = _old_sequence(hunk_lines)
    if not sequence:
        return preferred_start
    if _sequence_matches(original, preferred_start, sequence):
        return preferred_start
    matches = [
        start
        for start in range(min_start, len(original) - len(sequence) + 1)
        if _sequence_matches(original, start, sequence)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        first_context = sequence[0] if sequence else ""
        raise ValueError(f"Patch context mismatch: {first_context!r}")
    raise ValueError(f"Patch context is ambiguous: {sequence[0]!r}")


def _apply_hunk(
    original: list[str],
    hunk_lines: list[str],
    cursor: int,
    output: list[str],
    file_path: str,
) -> int:
    for line in hunk_lines:
        marker = line[:1]
        value = line[1:]
        if marker == " ":
            if cursor >= len(original) or original[cursor] != value:
                raise ValueError(f"Patch context mismatch in {file_path}: {value!r}")
            output.append(original[cursor])
            cursor += 1
        elif marker == "-":
            if cursor >= len(original) or original[cursor] != value:
                raise ValueError(f"Patch removal mismatch in {file_path}: {value!r}")
            cursor += 1
        elif marker == "+":
            output.append(value)
        else:
            raise ValueError(f"Unknown patch marker {marker!r}")
    return cursor
