from __future__ import annotations


def propose_policy_update(reason: str, target: str = "planner_policy") -> dict:
    return {
        "proposal_id": "policy_proposal_local",
        "target": target,
        "reason": reason,
        "proposed_change": "Inspect related tests before generating implementation patches.",
        "requires_human_approval": True,
    }

