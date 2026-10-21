from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.harness.reporter import ReportGenerator
from app.schemas.task import TaskRunResult


class ReporterTests(unittest.TestCase):
    def test_task_report_contains_full_validation_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace_path = root / "trace.jsonl"
            _write_trace(
                trace_path,
                [
                    {
                        "type": "task_started",
                        "payload": {
                            "task_id": "report_demo",
                            "repo_url": "https://github.com/example/repo",
                            "base_commit": "abc123",
                            "issue_title": "Fix bug",
                            "test_command": "pytest tests/test_demo.py",
                            "use_docker": True,
                            "allow_network": False,
                            "install_dependencies": True,
                            "max_iterations": 2,
                        },
                    },
                    {
                        "type": "harness_phase",
                        "payload": {"phase": "environment_setup", "duration_ms": 1250, "cache_hit": True},
                    },
                    {"type": "agent_phase", "payload": {"phase": "inspect_repo", "candidate_files": ["src/app.py"]}},
                    {
                        "type": "llm_patch_generation",
                        "payload": {
                            "provider": "deepseek",
                            "model": "fake",
                            "success": True,
                            "attempt": 1,
                            "prompt_mode": "full",
                            "diff_chars": 42,
                            "context_files": ["src/app.py"],
                        },
                    },
                    {
                        "type": "tool_call",
                        "payload": {
                            "tool_name": "run_tests",
                            "tool_output": {
                                "command": "pytest tests/test_demo.py",
                                "status": "passed",
                                "duration_ms": 100,
                                "error_summary": "ok",
                            },
                            "success": True,
                        },
                    },
                ],
            )
            result = TaskRunResult(
                task_id="report_demo",
                status="succeeded",
                patch_apply_success=True,
                tests_passed=True,
                iterations=1,
                duration_seconds=1.2,
                modified_files=["src/app.py"],
                trace_path=str(trace_path),
                environment_setup={"status": "succeeded", "enabled": True},
            )

            report_path = ReportGenerator().write_task_report(result, root)
            report = report_path.read_text(encoding="utf-8")

            self.assertIn("## 测试对象", report)
            self.assertIn("https://github.com/example/repo", report)
            self.assertIn("## 执行环境", report)
            self.assertIn("## 时间分解", report)
            self.assertIn("environment_setup", report)
            self.assertIn("1250ms", report)
            self.assertIn("## Patch 生成记录", report)
            self.assertIn("## 测试执行记录", report)
            self.assertIn("## 产物路径", report)


def _write_trace(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
