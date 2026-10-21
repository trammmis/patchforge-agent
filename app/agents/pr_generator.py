from __future__ import annotations


class PRGenerator:
    def generate(
        self,
        issue_title: str,
        diff: str,
        test_result: dict,
        reviewer_result: dict,
        issue_url: str | None = None,
    ) -> dict[str, str]:
        changed_files = reviewer_result.get("changed_files", [])
        title = f"Fix: {issue_title}".strip()
        tests = test_result.get("command", "未运行")
        body = "\n".join(
            [
                "## 修复摘要",
                reviewer_result.get("summary", "PatchForge 生成了一个代码修复 patch。"),
                "",
                "## 修改内容",
                *(f"- `{file}`" for file in changed_files),
                "",
                "## 测试结果",
                f"- `{tests}` -> {test_result.get('status', 'unknown')}",
                "",
                "## 风险评估",
                reviewer_result.get("risk", "unknown"),
                "",
                "## 关联 Issue",
                issue_url or "本地手动输入任务",
                "",
                "## 人工 Review 说明",
                "创建真实 PR 属于写操作，需要用户明确审批；当前版本只生成草稿，不会自动 push 或 merge。",
            ]
        )
        return {"title": title, "body": body}
