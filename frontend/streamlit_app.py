from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    import streamlit as st
except Exception as exc:  # pragma: no cover
    raise SystemExit("请先安装依赖：pip install -e \".[dev]\"") from exc

from app.harness.evaluation_harness import EvaluationHarness
from app.harness.execution_harness import ExecutionHarness
from app.harness.timeline import build_timeline
from app.schemas.eval import EvalRunConfig
from app.schemas.task import TaskInstance, TaskRunResult
from app.services.config import settings
from app.services.github import GitHubIssueError, fetch_issue
from app.storage.task_store import TaskStore

ROOT = Path.cwd()
RUNS_DIR = ROOT / ".patchforge_runs"
DATA_DIR = ROOT / ".patchforge_data"

DEMO_CASES = {
    "折扣：0 元价格返回 None": {
        "repo_path": "examples/demo_python_repo",
        "issue_title": "Fix zero price discount bug",
        "issue_body": "calculate_discount returns None when price is 0. It should return 0.0.",
        "test_command": "python -m unittest tests.test_pricing",
    },
    "除法：分母为 0 崩溃": {
        "repo_path": "examples/demo_python_repo",
        "issue_title": "Fix divide by zero crash",
        "issue_body": "safe_divide raises ZeroDivisionError for denominator 0; return None instead.",
        "test_command": "python -m unittest tests.test_division",
    },
    "字符串：None 名字崩溃": {
        "repo_path": "examples/demo_python_repo",
        "issue_title": "Fix normalize_name for None",
        "issue_body": "normalize_name crashes when value is None and should return an empty string.",
        "test_command": "python -m unittest tests.test_strings",
    },
    "集合：偶数长度 median 错误": {
        "repo_path": "examples/demo_python_repo",
        "issue_title": "Fix median for even length lists",
        "issue_body": "median([1, 3, 5, 9]) should average the two middle values.",
        "test_command": "python -m unittest tests.test_median",
    },
    "集合：去重不应改变顺序": {
        "repo_path": "examples/demo_python_repo",
        "issue_title": "Deduplicate should preserve order",
        "issue_body": "deduplicate currently sorts values; keep first-seen order instead.",
        "test_command": "python -m unittest tests.test_deduplicate",
    },
    "任务管理：优先级大小写": {
        "repo_path": "examples/task_manager_repo",
        "issue_title": "Validate priority should be case-insensitive",
        "issue_body": "validate_priority rejects HIGH; it should normalize accepted priority values to lowercase and return high.",
        "test_command": "python -m unittest tests.test_priority",
    },
    "任务管理：隐藏已完成任务": {
        "repo_path": "examples/task_manager_repo",
        "issue_title": "filter_tasks default should hide completed tasks",
        "issue_body": "By default filter_tasks should return only unfinished tasks and hide done tasks.",
        "test_command": "python -m unittest tests.test_filters",
    },
    "任务管理：保存时创建父目录": {
        "repo_path": "examples/task_manager_repo",
        "issue_title": "save_tasks should create parent directories",
        "issue_body": "save_tasks fails for a nested tasks.json path when the parent directory does not exist. Create missing parents before writing.",
        "test_command": "python -m unittest tests.test_storage",
    },
    "任务管理：忽略坏配置行": {
        "repo_path": "examples/task_manager_repo",
        "issue_title": "load_config should ignore malformed lines",
        "issue_body": "load_config raises ValueError for a line without '='. It should skip malformed lines while keeping valid key=value entries.",
        "test_command": "python -m unittest tests.test_config",
    },
}


st.set_page_config(page_title="PatchForge 控制台", page_icon="PF", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; max-width: 1220px; }
    div[data-testid="stMetric"] {
        background: #f7f7f5;
        border: 1px solid #e3e1db;
        border-radius: 8px;
        padding: 0.75rem 0.9rem;
    }
    .pf-note {
        border-left: 4px solid #2f6f73;
        background: #f4faf9;
        padding: 0.75rem 1rem;
        border-radius: 6px;
        color: #1d3436;
    }
    .pf-muted { color: #666; font-size: 0.92rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    st.title("PatchForge 控制台")
    st.caption("一个带 Execution Harness / Evaluation Harness / Evolution 资产的代码修复 Agent 原型")

    with st.sidebar:
        st.subheader("运行配置")
        repo_path = st.text_input("目标仓库路径", "examples/demo_python_repo")
        repo_url = st.text_input("远程仓库 URL（可选）", "")
        base_commit = st.text_input("远程仓库 ref（可选）", "")
        use_docker = st.checkbox("使用 Docker sandbox", value=False, help="默认关闭，便于本地快速演示。")
        install_dependencies = st.checkbox("运行前准备依赖", value=False, help="在任务 workspace 中创建 .patchforge_venv，并按依赖清单准备 Python 环境。")
        allow_network = st.checkbox("允许网络安装依赖", value=False, help="仅在确实需要 pip 下载依赖时开启。")
        st.divider()
        st.subheader("模型模式")
        llm_provider = getattr(settings, "llm_provider", "offline")
        deepseek_model = getattr(settings, "deepseek_model", "deepseek-v4-flash")
        deepseek_api_key = getattr(settings, "deepseek_api_key", "")
        provider_label = "DeepSeek" if llm_provider == "deepseek" else "离线规则"
        st.metric("当前 Provider", provider_label)
        st.caption(f"模型：{deepseek_model if llm_provider == 'deepseek' else getattr(settings, 'model', 'offline-rule-agent')}")
        if llm_provider == "deepseek" and deepseek_api_key:
            st.success("DeepSeek API key 已配置。")
        elif llm_provider == "deepseek":
            st.warning("已选择 DeepSeek，但未配置 DEEPSEEK_API_KEY。")
        else:
            st.info("当前使用离线规则模式；设置 PATCHFORGE_LLM_PROVIDER=deepseek 可启用 LLM patch 生成。")
        st.divider()
        st.markdown("**当前推荐验证路径**")
        st.code("patchforge run ...\npatchforge eval --dataset evals/tasks.jsonl\npatchforge eval --dataset evals/task_manager_tasks.jsonl", language="powershell")
        st.markdown('<p class="pf-muted">UI 运行不会修改原始 repo，只会操作 .patchforge_runs 下的 workspace 副本。</p>', unsafe_allow_html=True)

    tab_run, tab_eval, tab_history, tab_about = st.tabs(["单任务修复", "批量评测", "运行记录", "项目说明"])
    with tab_run:
        render_single_task(repo_path, repo_url, base_commit, use_docker, install_dependencies, allow_network)
    with tab_eval:
        render_eval(use_docker, install_dependencies, allow_network)
    with tab_history:
        render_history()
    with tab_about:
        render_about()


def render_single_task(
    repo_path: str,
    repo_url: str,
    base_commit: str,
    use_docker: bool,
    install_dependencies: bool,
    allow_network: bool,
) -> None:
    st.header("单任务修复")
    st.markdown('<div class="pf-note">选择一个内置 demo，或手动输入 Issue。点击运行后，PatchForge 会复制目标仓库、生成 patch、运行测试，并展示 diff 与 PR 草稿。</div>', unsafe_allow_html=True)
    st.write("")

    selected_case = st.selectbox("示例任务", list(DEMO_CASES), index=0)
    preset = DEMO_CASES[selected_case]
    case_key = selected_case.replace("：", "_").replace(" ", "_")
    task_repo_path = str(preset.get("repo_path", repo_path))
    github_issue_url = st.text_input("GitHub Issue URL（可选）", value="", key=f"github_issue_url_{case_key}")
    import_clicked = st.button("从 GitHub Issue 导入标题和描述", key=f"import_issue_{case_key}")
    if import_clicked:
        if not github_issue_url.strip():
            st.warning("请先输入 GitHub Issue URL。")
        else:
            try:
                issue = fetch_issue(github_issue_url)
                st.session_state[f"title_{case_key}"] = issue["issue_title"]
                st.session_state[f"body_{case_key}"] = issue["issue_body"]
                st.session_state[f"loaded_issue_url_{case_key}"] = issue["issue_url"]
                st.session_state[f"repo_{case_key}"] = ""
                st.session_state[f"repo_url_{case_key}"] = issue.get("repo_url", "")
                st.success(f"已导入 Issue #{issue.get('number', '')}：{issue['issue_title']}")
            except GitHubIssueError as exc:
                st.error(f"读取 GitHub Issue 失败：{exc}")
    col_left, col_right = st.columns([2, 1])
    with col_left:
        task_id = st.text_input("任务 ID", value=f"ui_{case_key}", key=f"task_id_{case_key}")
        issue_title = st.text_input("Issue 标题", value=preset["issue_title"], key=f"title_{case_key}")
        issue_body = st.text_area("Issue 描述", value=preset["issue_body"], height=110, key=f"body_{case_key}")
    with col_right:
        task_repo_path = st.text_input("本任务仓库路径", value=task_repo_path, key=f"repo_{case_key}")
        task_repo_url = st.text_input("本任务远程仓库 URL（可选）", value=repo_url, key=f"repo_url_{case_key}")
        task_base_commit = st.text_input("本任务远程 ref（可选）", value=base_commit, key=f"base_commit_{case_key}")
        test_command = st.text_input("测试命令", value=preset["test_command"], key=f"test_{case_key}")
        max_iterations = st.number_input("最大迭代轮数", min_value=1, max_value=8, value=3, step=1)
        st.write("")
        run_clicked = st.button("运行 PatchForge", type="primary", width="stretch")

    if run_clicked:
        loaded_issue_url = st.session_state.get(f"loaded_issue_url_{case_key}")
        issue_url = github_issue_url.strip() or str(loaded_issue_url or "")
        task = TaskInstance(
            task_id=task_id.strip() or "streamlit_demo",
            repo_path=task_repo_path.strip() or None,
            repo_url=task_repo_url.strip() or None,
            base_commit=task_base_commit.strip() or None,
            issue_url=issue_url or None,
            issue_title=issue_title,
            issue_body=issue_body,
            test_command=test_command,
            max_iterations=int(max_iterations),
            allow_network=allow_network,
            use_docker=use_docker,
            install_dependencies=install_dependencies,
        )
        with st.status("PatchForge 正在执行任务...", expanded=True) as status:
            store = TaskStore()
            store.create_task(task)
            started_at = time.time()
            store.mark_running(task.task_id, started_at)
            st.write("初始化 workspace")
            st.write("准备依赖环境")
            st.write("执行 Agent workflow")
            try:
                result = ExecutionHarness().run_task(task)
            except Exception as exc:
                store.mark_failed(
                    task.task_id,
                    time.time(),
                    failure_type="streamlit_run_error",
                    error_message=str(exc),
                )
                raise
            store.mark_finished(task.task_id, time.time(), result)
            st.write("生成报告与 PR 草稿")
            status.update(label="任务执行完成", state="complete", expanded=False)
        st.session_state["last_result"] = result.model_dump()

    result_payload = st.session_state.get("last_result")
    if result_payload:
        render_task_result(TaskRunResult.model_validate(result_payload))


def render_task_result(result: TaskRunResult) -> None:
    st.subheader("执行结果")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("任务状态", result.status)
    col2.metric("测试通过", "是" if result.tests_passed else "否")
    col3.metric("迭代轮数", result.iterations)
    col4.metric("修改文件数", len(result.modified_files))
    if result.status != "succeeded" and (result.failure_type or result.error_message):
        st.error(f"失败类型：{result.failure_type or 'unknown'}\n\n{result.error_message or '请查看 Trace 预览获取更多细节。'}")

    detail_tabs = st.tabs(["Diff", "环境准备", "审批请求", "运行时间线", "经验复用", "PR 草稿", "结构化结果", "Trace 预览"])
    with detail_tabs[0]:
        st.code(result.final_diff or "暂无 diff", language="diff")
    with detail_tabs[1]:
        render_environment_setup(result.environment_setup)
    with detail_tabs[2]:
        render_approval_requests(result)
    with detail_tabs[3]:
        render_timeline(result.trace_path, result.task_id)
    with detail_tabs[4]:
        render_experience_summary(result.experience_summary)
    with detail_tabs[5]:
        st.markdown(f"### {result.pr_title or '暂无标题'}")
        st.markdown(result.pr_body or "暂无 PR 草稿")
    with detail_tabs[6]:
        st.json(result.model_dump())
    with detail_tabs[7]:
        render_trace_preview(result.trace_path)


def render_experience_summary(summary: dict[str, Any]) -> None:
    if not summary:
        st.info("本次任务没有检索到可复用经验。")
        return
    cols = st.columns(4)
    cols[0].metric("策略", summary.get("retrieval_strategy", "unknown"))
    cols[1].metric("Reflection", summary.get("reflection_count", 0))
    cols[2].metric("Skill", summary.get("skill_count", 0))
    cols[3].metric("Failure Pattern", summary.get("failure_pattern_count", 0))
    rows = []
    for category, key in [("Reflection", "reflections"), ("Skill", "skills"), ("Failure Pattern", "failure_patterns")]:
        for value in summary.get(key) or []:
            rows.append({"类型": category, "命中项": value})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch")
    else:
        st.caption("没有可展示的具体命中项。")


def render_environment_setup(summary: dict[str, Any]) -> None:
    if not summary:
        st.info("本次任务未启用依赖准备。")
        return
    cols = st.columns(4)
    cols[0].metric("状态", summary.get("status", "unknown"))
    cols[1].metric("启用", "是" if summary.get("enabled") else "否")
    cols[2].metric("依赖清单", len(summary.get("manifests") or []))
    cols[3].metric("命令数", len(summary.get("commands") or []))
    st.caption(summary.get("message") or "")
    if summary.get("manifests"):
        st.write("依赖清单：", "、".join(summary.get("manifests") or []))
    if summary.get("skipped"):
        st.warning("；".join(str(item) for item in summary.get("skipped") or []))
    commands = summary.get("commands") or []
    if commands:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "command": item.get("command"),
                        "returncode": item.get("returncode"),
                        "message": item.get("message"),
                        "duration_ms": item.get("duration_ms"),
                    }
                    for item in commands
                ]
            ),
            width="stretch",
        )


def render_approval_requests(result: TaskRunResult) -> None:
    requests = result.approval_requests
    if not requests:
        st.info("本次任务没有触发高风险审批。")
        return
    st.warning("PatchForge 已阻止自动应用高风险 patch，需要人工审查后再继续。")
    rows = []
    for item in requests:
        rows.append(
            {
                "iteration": item.get("iteration"),
                "approved": item.get("approved", False),
                "changed_files": ", ".join(item.get("changed_files") or []),
                "risk_reasons": "; ".join(item.get("risk_reasons") or []),
                "reason": item.get("reason", ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch")
    with st.expander("查看待审批 diff"):
        for index, item in enumerate(requests, 1):
            st.markdown(f"**审批项 {index}**")
            st.code(item.get("diff") or "", language="diff")
    if result.status == "needs_approval":
        notes = st.text_area("审批备注", value="", key=f"approval_notes_{result.task_id}")
        approve_col, reject_col = st.columns(2)
        if approve_col.button("批准并继续执行", type="primary", key=f"approve_{result.task_id}"):
            resumed = _handle_streamlit_approval(result, approved=True, notes=notes)
            st.session_state["last_result"] = resumed.model_dump()
            st.rerun()
        if reject_col.button("拒绝 patch", key=f"reject_{result.task_id}"):
            resumed = _handle_streamlit_approval(result, approved=False, notes=notes)
            st.session_state["last_result"] = resumed.model_dump()
            st.rerun()


def _handle_streamlit_approval(result: TaskRunResult, approved: bool, notes: str) -> TaskRunResult:
    store = TaskStore()
    task = store.get_task(result.task_id)
    started_at = time.time()
    store.mark_running(result.task_id, started_at)
    try:
        resumed = ExecutionHarness().resume_after_approval(
            task,
            result,
            approved=approved,
            reviewer="streamlit-user",
            notes=notes,
        )
    except Exception as exc:
        store.mark_failed(
            result.task_id,
            time.time(),
            failure_type="streamlit_approval_error",
            error_message=str(exc),
        )
        raise
    store.mark_finished(result.task_id, time.time(), resumed)
    return resumed


def render_timeline(trace_path: str | None, task_id: str | None = None) -> None:
    if not trace_path:
        st.info("暂无运行时间线。")
        return
    timeline = build_timeline(task_id or Path(trace_path).parent.name, trace_path)
    if not timeline.items:
        st.info("时间线为空，任务可能尚未开始写入 trace。")
        return
    st.progress(timeline.progress)
    st.caption(f"当前阶段：{timeline.latest_phase}；事件数：{len(timeline.items)}")
    rows = [
        {
            "#": item.index,
            "耗时(s)": item.elapsed_seconds,
            "类别": item.category,
            "状态": item.status,
            "阶段/工具": item.phase or item.tool_name or "",
            "标题": item.title,
            "说明": item.detail,
            "duration_ms": item.duration_ms,
        }
        for item in timeline.items[-80:]
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch")


def render_eval(use_docker: bool, install_dependencies: bool, allow_network: bool) -> None:
    st.header("批量评测")
    dataset_choice = st.selectbox("推荐数据集", ["evals/tasks.jsonl", "evals/task_manager_tasks.jsonl"], index=0)
    custom_dataset = st.text_input("自定义数据集路径（可选）", "")
    dataset = custom_dataset.strip() or dataset_choice
    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        model = st.text_input("模型版本", "offline-rule-agent")
    with col_b:
        policy_version = st.text_input("策略版本", "v1")
    with col_c:
        st.write("")
        run_eval = st.button("运行评测", type="primary", width="stretch")

    if run_eval:
        config = EvalRunConfig(
            dataset=dataset,
            model=model,
            policy_version=policy_version,
            use_docker=use_docker,
            install_dependencies=install_dependencies,
            allow_network=allow_network,
        )
        with st.status("正在运行 Evaluation Harness...", expanded=True) as status:
            result = EvaluationHarness().run(config)
            status.update(label="评测完成", state="complete", expanded=False)
        st.session_state["last_eval"] = result.model_dump()

    eval_payload = st.session_state.get("last_eval")
    if eval_payload:
        metrics = eval_payload.get("metrics", {})
        cols = st.columns(4)
        cols[0].metric("Task Success Rate", metrics.get("Task Success Rate", 0))
        cols[1].metric("Patch Apply Rate", metrics.get("Patch Apply Rate", 0))
        cols[2].metric("Test Pass Rate", metrics.get("Test Pass Rate", 0))
        cols[3].metric("Total Cases", metrics.get("Total Cases", 0))

        st.subheader("指标明细")
        st.dataframe(pd.DataFrame([{"指标": key, "数值": str(value)} for key, value in metrics.items()]), width="stretch")

        st.subheader("任务结果")
        rows = []
        for item in eval_payload.get("results", []):
            rows.append(
                {
                    "task_id": item.get("task_id"),
                    "status": item.get("status"),
                    "tests_passed": item.get("tests_passed"),
                    "iterations": item.get("iterations"),
                    "environment": (item.get("environment_setup") or {}).get("status", ""),
                    "modified_files": ", ".join(item.get("modified_files") or []),
                    "report_path": item.get("report_path"),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch")
        if eval_payload.get("report_path"):
            st.info(f"评测报告：{eval_payload['report_path']}")


def render_history() -> None:
    st.header("运行记录")
    statuses = TaskStore().list_statuses(limit=100)
    if statuses:
        rows = [
            {
                "task_id": item.task_id,
                "status": item.status,
                "phase": item.current_phase,
                "iterations": item.iteration_count,
                "failure": item.failure_type or "",
                "report_path": item.report_path or "",
            }
            for item in statuses
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch")
        selected = st.selectbox("选择历史任务", [item.task_id for item in statuses])
        selected_status = next(item for item in statuses if item.task_id == selected)
        trace_path = Path(selected_status.trace_path or RUNS_DIR / selected / "trace.jsonl")
        report_path = Path(selected_status.report_path) if selected_status.report_path else None
        reports = [report_path] if report_path and report_path.exists() else []
    else:
        if not RUNS_DIR.exists():
            st.info("还没有运行记录。先到「单任务修复」运行一个任务。")
            return
        task_dirs = sorted([path for path in RUNS_DIR.iterdir() if path.is_dir()], key=lambda item: item.stat().st_mtime, reverse=True)
        if not task_dirs:
            st.info("还没有运行记录。")
            return
        selected = st.selectbox("选择历史任务", [path.name for path in task_dirs])
        task_dir = RUNS_DIR / selected
        reports = sorted((task_dir / "reports").glob("*_report.md")) if (task_dir / "reports").exists() else []
        trace_path = task_dir / "trace.jsonl"

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("报告")
        if reports:
            st.markdown(reports[0].read_text(encoding="utf-8"))
        else:
            st.warning("这个任务没有找到 Markdown 报告。")
    with col2:
        st.subheader("运行时间线")
        render_timeline(str(trace_path) if trace_path.exists() else None, selected)
        st.subheader("Trace")
        render_trace_preview(str(trace_path) if trace_path.exists() else None)


def render_about() -> None:
    st.header("项目说明")
    st.markdown(
        """
        PatchForge 当前完成的是一个可运行的 SWE Agent MVP：

        - **Agent Layer**：负责 Issue 解析、仓库探索、计划、patch 生成、错误分析和 PR 草稿。
        - **Harness Layer**：负责 workspace、受控工具、测试执行、trace、报告和评测。
        - **Evolution Layer**：从成功/失败轨迹中生成 reflection、skill 和 failure pattern，并在新任务开始时用本地语义向量检索相似经验注入 Planner。
        - **GitHub Issue 导入**：可以从公开 Issue URL 读取标题和正文；当前仍只修复本地 repo 副本并生成 PR 草稿。
        - **LLM Patch Loop**：DeepSeek 生成 diff 后先由 Harness 校验；如果出现 context mismatch，
          下一轮会携带失败原因和最新文件内容，让模型重写可应用的 patch。

        当前 CodeEditor 已支持 DeepSeek-first 和规则 fallback 两种模式。默认离线模式适合稳定演示；
        配置 `PATCHFORGE_LLM_PROVIDER=deepseek` 后，会优先调用 DeepSeek 生成 patch。
        单任务结果里的「经验复用」标签页会显示本次命中的历史 reflection、skill、failure pattern 和检索策略。
        """
    )
    st.subheader("推荐命令")
    st.code(
        """pip install -e ".[dev]"
python -m unittest discover -s tests
patchforge run --task-id demo_discount --repo examples/demo_python_repo --issue-title "Fix zero price discount bug" --issue-body "calculate_discount returns None when price is 0." --test-command "python -m unittest tests.test_pricing"
patchforge eval --dataset evals/tasks.jsonl""",
        language="powershell",
    )


def render_trace_preview(trace_path: str | None) -> None:
    if not trace_path:
        st.info("暂无 trace 文件。")
        return
    path = Path(trace_path)
    if not path.exists():
        st.warning(f"找不到 trace 文件：{trace_path}")
        return
    events = read_jsonl(path)
    if not events:
        st.info("trace 文件为空。")
        return
    preview_rows = []
    for event in events[-30:]:
        payload = event.get("payload", {})
        preview_rows.append(
            {
                "type": event.get("type"),
                "tool/phase": payload.get("tool_name") or payload.get("phase") or "",
                "success": str(payload.get("success", "")),
                "duration_ms": str(payload.get("duration_ms", "")),
            }
        )
    st.dataframe(pd.DataFrame(preview_rows), width="stretch")
    with st.expander("查看原始 trace JSONL"):
        st.code("\n".join(json.dumps(item, ensure_ascii=False) for item in events[-20:]), language="json")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"type": "invalid_json", "payload": {"line": line}})
    return rows


if __name__ == "__main__":
    main()
