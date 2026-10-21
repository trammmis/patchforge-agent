"""LangGraph-ready node placeholders.

The MVP uses the sequential orchestrator in app.agents.orchestrator. These
function names mirror the planned graph nodes so a LangGraph workflow can be
introduced without changing public state.
"""

NODE_NAMES = [
    "parse_issue",
    "retrieve_experience",
    "inspect_repo",
    "create_plan",
    "retrieve_context",
    "generate_patch",
    "apply_patch",
    "run_tests",
    "analyze_test_result",
    "review_patch",
    "fail_summary",
]

