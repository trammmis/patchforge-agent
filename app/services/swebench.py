from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from app.schemas.eval import EvalTask

SWEBENCH_PARQUET_URLS = {
    "verified": "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet",
}


class SweBenchImportError(RuntimeError):
    """Raised when SWE-bench metadata cannot be imported."""


def import_swebench_eval_dataset(
    instance_ids: list[str],
    output_path: Path | str,
    *,
    dataset: str = "verified",
    test_command: str = "pytest",
    use_docker: bool = False,
    docker_image: str | None = None,
    install_dependencies: bool = False,
    allow_network: bool = False,
    max_iterations: int = 3,
    append: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    normalized_ids = _normalize_instance_ids(instance_ids)
    if not normalized_ids:
        raise SweBenchImportError("请至少提供一个 SWE-bench instance id。")

    target = Path(output_path)
    if target.exists() and not append and not overwrite:
        raise SweBenchImportError(f"{target} 已存在；请使用 append 或 overwrite 明确写入方式。")

    rows = load_swebench_rows(normalized_ids, dataset=dataset)
    tasks = [
        eval_task_from_swebench_row(
            row,
            test_command=test_command,
            use_docker=use_docker,
            docker_image=docker_image,
            install_dependencies=install_dependencies,
            allow_network=allow_network,
            max_iterations=max_iterations,
        )
        for row in rows
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and target.exists() and not overwrite else "w"
    with target.open(mode, encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task.model_dump(), ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "output_path": str(target),
        "dataset": dataset,
        "imported": len(tasks),
        "instance_ids": [task.task_id for task in tasks],
        "mode": "append" if mode == "a" else "overwrite",
    }


def load_swebench_rows(instance_ids: list[str], *, dataset: str = "verified") -> list[dict[str, Any]]:
    source_url = SWEBENCH_PARQUET_URLS.get(dataset)
    if not source_url:
        raise SweBenchImportError(f"暂不支持 SWE-bench 数据集：{dataset}")
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except Exception as exc:
        raise SweBenchImportError("导入 SWE-bench 需要 pandas 和 pyarrow；请先安装后重试。") from exc

    try:
        frame = pd.read_parquet(source_url)
    except Exception as exc:
        raise SweBenchImportError(f"读取 SWE-bench 元数据失败：{exc}") from exc

    rows_by_id = {str(row["instance_id"]): dict(row) for _, row in frame.iterrows()}
    missing = [instance_id for instance_id in instance_ids if instance_id not in rows_by_id]
    if missing:
        raise SweBenchImportError(f"未找到 instance id：{', '.join(missing)}")
    return [rows_by_id[instance_id] for instance_id in instance_ids]


def eval_task_from_swebench_row(
    row: dict[str, Any],
    *,
    test_command: str = "pytest",
    use_docker: bool = False,
    docker_image: str | None = None,
    install_dependencies: bool = False,
    allow_network: bool = False,
    max_iterations: int = 3,
) -> EvalTask:
    instance_id = str(row.get("instance_id") or "").strip()
    repo = str(row.get("repo") or "").strip()
    problem = str(row.get("problem_statement") or "").strip()
    if not instance_id or not repo:
        raise SweBenchImportError("SWE-bench row 缺少 instance_id 或 repo。")
    title, body = _split_problem_statement(problem)
    validation_test_command = _validation_command_from_fail_to_pass(row.get("FAIL_TO_PASS"))
    return EvalTask(
        task_id=instance_id,
        repo_url=f"https://github.com/{repo}",
        base_commit=str(row.get("base_commit") or "").strip() or None,
        issue_url=None,
        issue_title=title or instance_id,
        issue_body=body or problem,
        test_command=_primary_test_command(test_command, validation_test_command),
        expected_modified_files=_expected_files_from_patch(str(row.get("patch") or "")) or None,
        validation_patch=str(row.get("test_patch") or "").strip() or None,
        validation_test_command=validation_test_command,
        max_steps=30,
        max_iterations=max_iterations,
        use_docker=use_docker,
        docker_image=docker_image,
        install_dependencies=install_dependencies,
        allow_network=allow_network,
    )


def parse_instance_ids(values: list[str]) -> list[str]:
    ids: list[str] = []
    for value in values:
        ids.extend(part.strip() for part in value.split(",") if part.strip())
    return _normalize_instance_ids(ids)


def _normalize_instance_ids(instance_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for instance_id in instance_ids:
        value = instance_id.strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _split_problem_statement(problem: str) -> tuple[str, str]:
    lines = [line.rstrip() for line in problem.splitlines()]
    title = next((line for line in lines if line.strip()), "")
    title_index = lines.index(title) if title else -1
    body = "\n".join(lines[title_index + 1 :]).strip() if title_index >= 0 else ""
    return title, body


def _expected_files_from_patch(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            file_path = line[6:]
            if file_path and file_path not in files:
                files.append(file_path)
    return files


def _validation_command_from_fail_to_pass(value: Any) -> str | None:
    tests = _string_list(value)
    if not tests:
        return None
    return "python -m pytest " + " ".join(tests)


def _primary_test_command(command: str, validation_command: str | None) -> str:
    """Avoid an unbounded full-suite run when SWE-bench provides focused tests."""
    if not validation_command or not _is_bare_pytest(command):
        return command
    try:
        tokens = shlex.split(validation_command)
    except ValueError:
        return command
    files: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            continue
        path = token.split("::", 1)[0]
        if path.endswith((".py", ".pyc")) and path not in files:
            files.append(path)
    return "python -m pytest " + " ".join(files) if files else command


def _is_bare_pytest(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return tokens in (["pytest"], ["python", "-m", "pytest"])


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return _string_list(parsed)
    return []
