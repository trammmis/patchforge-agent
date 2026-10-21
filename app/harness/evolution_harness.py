from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.retrieval.experience_vector_index import ExperienceVectorIndex
from app.schemas.evolution import FailurePattern, ReflectionRecord, SkillRecord
from app.schemas.task import TaskRunResult
from app.services.config import settings


class EvolutionHarness:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else settings.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reflections_path = self.data_dir / "reflections.jsonl"
        self.skills_path = self.data_dir / "skills.jsonl"
        self.failures_path = self.data_dir / "failure_patterns.jsonl"
        self.vector_index = ExperienceVectorIndex()

    def ingest_result(self, result: TaskRunResult) -> None:
        reflection = ReflectionRecord(
            task_id=result.task_id,
            success=result.status == "succeeded",
            reflection=self._reflection_text(result),
            applicable_future_tasks=result.modified_files,
            confidence=0.8 if result.status == "succeeded" else 0.55,
        )
        self._append(self.reflections_path, reflection.model_dump())
        if result.status == "succeeded":
            skill = SkillRecord(
                skill_id=f"skill_{result.task_id}",
                name=f"Repair pattern from {result.task_id}",
                trigger_conditions=result.modified_files,
                procedure=[
                    "Search exact issue terms",
                    "Inspect implementation and focused tests",
                    "Apply a minimal unified diff",
                    "Run the focused test command",
                    "Review final diff before PR draft",
                ],
                preferred_tools=["search_code", "read_file", "apply_patch", "run_tests"],
                validated_on=[result.task_id],
                success_rate=1.0,
            )
            self._append(self.skills_path, skill.model_dump())
        else:
            pattern = FailurePattern(
                failure_type=result.failure_type or "unknown",
                symptoms=[result.task_id],
                likely_causes=["PatchForge could not complete the repair under current policy"],
                recommended_recovery=["Inspect trace", "Add a specific skill or eval case", "Rerun after policy update"],
            )
            self._append(self.failures_path, pattern.model_dump())

    def retrieve(self, query: str, limit: int = 3) -> dict[str, list[dict]]:
        tokens = _query_tokens(query)
        return {
            "reflections": self._retrieve_from_path(self.reflections_path, query, tokens, limit),
            "skills": self._retrieve_from_path(self.skills_path, query, tokens, limit),
            "failure_patterns": self._retrieve_from_path(self.failures_path, query, tokens, limit),
        }

    def _reflection_text(self, result: TaskRunResult) -> str:
        if result.status == "succeeded":
            return f"{result.task_id} succeeded after {result.iterations} iterations by changing {', '.join(result.modified_files)}."
        return f"{result.task_id} failed with {result.failure_type or 'unknown'}; inspect trace before retry."

    def _append(self, path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _retrieve_from_path(self, path: Path, query: str, tokens: list[str], limit: int) -> list[dict]:
        if settings.evolution_retrieval == "keyword":
            return self._search(path, tokens, limit)
        semantic_matches = self.vector_index.search(path, query, limit)
        if semantic_matches or settings.evolution_retrieval == "semantic":
            return semantic_matches
        return self._search(path, tokens, limit)

    def _search(self, path: Path, tokens: list[str], limit: int) -> list[dict]:
        if not path.exists():
            return []
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            score = _score_record(payload, tokens)
            if score >= 2:
                scored.append((score, len(scored), payload))
        scored.sort(key=lambda item: (-item[0], item[1]))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, _, payload in scored:
            key = _record_key(payload)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(payload)
            if len(deduped) >= limit:
                break
        return deduped


def _query_tokens(query: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "should",
        "return",
        "instead",
        "tests",
        "task",
        "tasks",
        "issue",
        "patch",
        "focused",
        "command",
        "repair",
        "pattern",
        "valid",
        "value",
        "python",
        "unittest",
    }
    raw_tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]{2,}", query.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if token in stopwords or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _score_record(payload: dict[str, Any], tokens: list[str]) -> int:
    if not tokens:
        return 0
    haystack = json.dumps(payload, ensure_ascii=False).lower()
    match_score = 0
    for token in tokens:
        if token.endswith(".py"):
            if token in haystack:
                match_score += 7
            continue
        if "/" in token:
            if token in haystack:
                match_score += 5
            continue
        if token in haystack:
            match_score += 1
    if match_score == 0:
        return 0
    score = match_score
    if payload.get("success") is True:
        score += 1
    if payload.get("success_rate", 0) == 1.0:
        score += 1
    return score


def _record_key(payload: dict[str, Any]) -> str:
    for key in ["skill_id", "task_id", "failure_type"]:
        value = payload.get(key)
        if value:
            return f"{key}:{value}"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
