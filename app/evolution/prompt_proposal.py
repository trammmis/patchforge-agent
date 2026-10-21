from __future__ import annotations


def prompt_proposal(summary: str) -> dict[str, str]:
    return {"target": "agent_system_prompt", "proposal": summary, "status": "draft"}

