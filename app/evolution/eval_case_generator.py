from __future__ import annotations


def propose_eval_case(task_id: str, issue_title: str, failure_type: str | None = None) -> dict:
    return {
        "task_id": f"generated_{task_id}",
        "issue_title": issue_title,
        "failure_type": failure_type or "unknown",
        "status": "proposal",
    }

