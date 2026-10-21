from __future__ import annotations


class Reviewer:
    def review(self, diff: str, test_result: dict, expected_modified_files: list[str] | None = None) -> dict:
        changed_files = [line[6:] for line in diff.splitlines() if line.startswith("+++ b/")]
        concerns = []
        if not diff.strip():
            concerns.append("No final diff was produced")
        if test_result.get("status") != "passed":
            concerns.append("Focused tests did not pass")
        if expected_modified_files:
            unexpected = sorted(set(changed_files) - set(expected_modified_files))
            if unexpected:
                concerns.append(f"Unexpected files modified: {', '.join(unexpected)}")
        return {
            "approved": not concerns,
            "risk": "low" if not concerns else "medium",
            "summary": "Patch 修改范围较小，测试已通过，适合进入人工 review。" if not concerns else "Patch 仍需要人工 review。",
            "changed_files": changed_files,
            "concerns": concerns,
        }
