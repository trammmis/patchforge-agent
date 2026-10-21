from __future__ import annotations

from app.schemas.task import AgentState


def route_after_tests(state: AgentState) -> str:
    if state.test_results and state.test_results[-1].get("status") == "passed":
        return "review_patch"
    if state.iteration_count >= state.max_iterations:
        return "fail_summary"
    return "analyze_error"

