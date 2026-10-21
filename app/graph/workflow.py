from __future__ import annotations

from app.agents.orchestrator import PatchForgeAgentOrchestrator


def build_workflow() -> PatchForgeAgentOrchestrator:
    """Return the active workflow object.

    LangGraph is listed as the intended orchestration dependency. The current
    implementation returns the deterministic sequential workflow so the project
    is runnable without optional packages installed.
    """

    return PatchForgeAgentOrchestrator()

