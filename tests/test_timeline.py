from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.harness.timeline import build_timeline
from app.harness.trace_recorder import TraceRecorder


class TimelineTests(unittest.TestCase):
    def test_builds_human_readable_timeline_from_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = TraceRecorder(Path(tmp) / "trace.jsonl")
            trace.record("task_started", {"task_id": "timeline_demo", "issue_title": "Fix bug", "test_command": "pytest"})
            trace.record("harness_phase", {"phase": "environment_setup", "duration_ms": 25, "cache_hit": True})
            trace.record("agent_phase", {"phase": "inspect_repo", "candidate_files": ["src/app.py"]})
            trace.record(
                "tool_call",
                {
                    "tool_name": "run_tests",
                    "tool_input": {"command": "pytest"},
                    "success": False,
                    "duration_ms": 12,
                },
            )
            trace.record("task_finished", {"status": "failed", "tests_passed": False, "failure_type": "logic_error"})

            timeline = build_timeline("timeline_demo", Path(tmp) / "trace.jsonl")

            self.assertTrue(timeline.terminal)
            self.assertEqual(timeline.progress, 1.0)
            self.assertEqual(timeline.latest_phase, "finished")
            self.assertEqual(timeline.items[0].title, "任务启动")
            self.assertEqual(timeline.items[1].title, "耗时统计：environment_setup")
            self.assertEqual(timeline.items[2].title, "探索仓库")
            self.assertEqual(timeline.items[3].title, "执行测试命令")
            self.assertEqual(timeline.items[-1].status, "failed")

    def test_timeline_includes_approval_required_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = TraceRecorder(Path(tmp) / "trace.jsonl")
            trace.record("task_started", {"task_id": "approval_demo", "issue_title": "Update deps"})
            trace.record(
                "approval_required",
                {
                    "changed_files": ["requirements.txt"],
                    "risk_reasons": ["high-risk file modified: requirements.txt"],
                },
            )
            trace.record("task_finished", {"status": "needs_approval", "tests_passed": False})

            timeline = build_timeline("approval_demo", Path(tmp) / "trace.jsonl")

            approval = [item for item in timeline.items if item.category == "approval"][0]
            self.assertEqual(approval.title, "等待人工审批")
            self.assertEqual(approval.status, "blocked")
            self.assertIn("requirements.txt", approval.detail)


if __name__ == "__main__":
    unittest.main()
