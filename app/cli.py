from __future__ import annotations

import argparse
import json
import sys

from app.harness.evaluation_harness import EvaluationHarness
from app.harness.execution_harness import ExecutionHarness
from app.schemas.eval import EvalRunConfig
from app.schemas.task import TaskInstance
from app.services.github import GitHubIssueError, fetch_issue
from app.services.swebench import SweBenchImportError, import_swebench_eval_dataset, parse_instance_ids


def _print_json(payload: object, *, stream=None) -> None:
    target = stream or sys.stdout
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()  # type: ignore[union-attr]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    try:
        print(rendered, file=target)
    except UnicodeEncodeError:
        # Windows legacy consoles may use GBK and reject characters from Issue text.
        print(json.dumps(payload, ensure_ascii=True, indent=2, default=str), file=target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patchforge", description="PatchForge：带 Harness 的本地代码修复 Agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="运行一个本地代码修复任务")
    run_parser.add_argument("--task-id", default="demo_task")
    run_parser.add_argument("--repo", default="", help="目标本地仓库路径")
    run_parser.add_argument("--repo-url", default="", help="远程 GitHub 仓库 URL；未提供 --repo 时自动下载到 workspace")
    run_parser.add_argument("--base-commit", default="", help="远程仓库 ref，可传分支名、tag 或 commit SHA")
    run_parser.add_argument("--issue-url", default="", help="GitHub Issue URL；提供后会自动读取标题和正文")
    run_parser.add_argument("--issue-title", default="", help="Issue 标题或任务摘要")
    run_parser.add_argument("--issue-body", default="", help="Issue 详细描述")
    run_parser.add_argument("--test-command", default="pytest", help="用于验证修复的测试命令")
    run_parser.add_argument("--max-iterations", type=int, default=3, help="最大修复迭代轮数")
    run_parser.add_argument("--docker", action="store_true", help="在 Docker sandbox 中执行测试命令")
    run_parser.add_argument("--docker-image", default="", help="Docker sandbox 镜像，例如 python:3.8-slim；未提供时使用默认配置")
    run_parser.add_argument("--install-dependencies", action="store_true", help="在任务 workspace 中创建 venv 并尝试准备 Python 依赖")
    run_parser.add_argument("--allow-network", action="store_true", help="允许依赖安装和 Docker sandbox 访问网络")

    eval_parser = subcommands.add_parser("eval", help="运行批量评测数据集")
    eval_parser.add_argument("--dataset", default="evals/tasks.jsonl", help="JSONL eval 数据集路径")
    eval_parser.add_argument("--model", default="offline-rule-agent", help="模型/策略标识")
    eval_parser.add_argument("--policy-version", default="v1", help="策略版本")
    eval_parser.add_argument("--docker", action="store_true", help="在 Docker sandbox 中执行测试命令")
    eval_parser.add_argument("--docker-image", default="", help="Docker sandbox 镜像，例如 python:3.8-slim；未提供时使用默认配置")
    eval_parser.add_argument("--install-dependencies", action="store_true", help="评测任务运行前准备 Python 依赖")
    eval_parser.add_argument("--allow-network", action="store_true", help="允许评测中的依赖安装访问网络")

    swebench_parser = subcommands.add_parser("swebench-import", help="从 SWE-bench 元数据导入 eval JSONL")
    swebench_parser.add_argument("--instance-id", action="append", default=[], help="SWE-bench instance id，可重复传入，也可用逗号分隔")
    swebench_parser.add_argument("--dataset", default="verified", choices=["verified"], help="SWE-bench 数据集")
    swebench_parser.add_argument("--output", default="evals/real_python_tasks.jsonl", help="输出 JSONL 路径")
    swebench_parser.add_argument("--test-command", default="pytest", help="写入 eval case 的测试命令")
    swebench_parser.add_argument("--max-iterations", type=int, default=3, help="每个任务最大修复迭代轮数")
    swebench_parser.add_argument("--docker", action="store_true", help="导入时标记任务使用 Docker sandbox")
    swebench_parser.add_argument("--docker-image", default="", help="导入时写入任务级 Docker 镜像")
    swebench_parser.add_argument("--install-dependencies", action="store_true", help="导入时标记任务需要依赖准备")
    swebench_parser.add_argument("--allow-network", action="store_true", help="导入时标记任务允许网络")
    swebench_parser.add_argument("--append", action="store_true", help="追加到已有 JSONL")
    swebench_parser.add_argument("--overwrite", action="store_true", help="覆盖已有 JSONL")

    args = parser.parse_args(argv)
    if args.command == "run":
        issue_url = args.issue_url.strip()
        issue_title = args.issue_title
        issue_body = args.issue_body
        repo_url = args.repo_url.strip() or None
        if issue_url:
            try:
                issue = fetch_issue(issue_url)
            except GitHubIssueError as exc:
                _print_json({"error": str(exc), "issue_url": issue_url}, stream=sys.stderr)
                return 1
            issue_title = issue_title or issue["issue_title"]
            issue_body = issue_body or issue["issue_body"]
            repo_url = repo_url or issue.get("repo_url")
            issue_url = issue["issue_url"]
        if not issue_title:
            _print_json({"error": "请提供 --issue-title 或 --issue-url"}, stream=sys.stderr)
            return 2
        if not args.repo.strip() and not repo_url:
            _print_json({"error": "请提供 --repo、--repo-url，或传入可解析仓库的 --issue-url"}, stream=sys.stderr)
            return 2
        task = TaskInstance(
            task_id=args.task_id,
            repo_url=repo_url,
            repo_path=args.repo.strip() or None,
            base_commit=args.base_commit.strip() or None,
            issue_url=issue_url or None,
            issue_title=issue_title,
            issue_body=issue_body,
            test_command=args.test_command,
            max_iterations=args.max_iterations,
            allow_network=args.allow_network,
            use_docker=args.docker,
            docker_image=args.docker_image.strip() or None,
            install_dependencies=args.install_dependencies,
        )
        result = ExecutionHarness().run_task(task)
        _print_json(result)
        return 0 if result.status == "succeeded" else 1
    if args.command == "eval":
        config = EvalRunConfig(
            dataset=args.dataset,
            model=args.model,
            policy_version=args.policy_version,
            use_docker=args.docker,
            docker_image=args.docker_image.strip() or None,
            install_dependencies=args.install_dependencies,
            allow_network=args.allow_network,
        )
        result = EvaluationHarness().run(config)
        _print_json(result)
        return 0 if result.metrics.get("Task Success Rate") == 1 else 1
    if args.command == "swebench-import":
        try:
            result = import_swebench_eval_dataset(
                parse_instance_ids(args.instance_id),
                args.output,
                dataset=args.dataset,
                test_command=args.test_command,
                use_docker=args.docker,
                docker_image=args.docker_image.strip() or None,
                install_dependencies=args.install_dependencies,
                allow_network=args.allow_network,
                max_iterations=args.max_iterations,
                append=args.append,
                overwrite=args.overwrite,
            )
        except SweBenchImportError as exc:
            _print_json({"error": str(exc)}, stream=sys.stderr)
            return 1
        _print_json(result)
        return 0
    _print_json({"error": "未知命令"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
