from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VECTOR_DIMENSIONS = 512
MIN_SEMANTIC_SCORE = 0.08

STOPWORDS = {
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
    "focused",
    "command",
    "repair",
    "pattern",
    "valid",
    "value",
    "python",
    "unittest",
}

SYNONYM_GROUPS = [
    {"config", "configuration", "settings", "setting", "env", "environment"},
    {"malformed", "invalid", "bad", "broken", "wrong"},
    {"line", "lines", "row", "rows", "entry", "entries"},
    {"priority", "importance", "severity"},
    {"uppercase", "upper", "capital", "case", "case-insensitive", "case_insensitive"},
    {"filter", "filters", "hide", "show", "include", "exclude"},
    {"completed", "complete", "done", "closed", "finished"},
    {"storage", "save", "saving", "persist", "persistence", "write", "file"},
    {"parent", "directory", "directories", "folder", "nested", "path"},
    {"discount", "price", "pricing", "zero"},
    {"divide", "division", "denominator", "zero"},
    {"normalize", "normalization", "name", "none", "null"},
    {"deduplicate", "duplicate", "duplicates", "order", "stable"},
    {"median", "middle", "average", "even"},
    {"patch", "diff", "apply", "context", "mismatch"},
]

ALIAS_TO_GROUP: dict[str, set[str]] = {}
for group in SYNONYM_GROUPS:
    normalized = {item.replace("-", "_") for item in group}
    for item in normalized:
        ALIAS_TO_GROUP[item] = normalized


@dataclass(frozen=True)
class IndexedExperience:
    payload: dict[str, Any]
    vector: dict[int, float]
    order: int


class ExperienceVectorIndex:
    """Small dependency-free semantic index for stored evolution records."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[tuple[int, int], list[IndexedExperience]]] = {}

    def search(self, path: Path, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        query_vector = vectorize_text(query)
        if not query_vector:
            return []
        records = self._records(path)
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for record in records:
            score = cosine_similarity(query_vector, record.vector)
            if score >= MIN_SEMANTIC_SCORE:
                scored.append((score, record.order, record.payload))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, _, payload in scored:
            key = _record_key(payload)
            if key in seen:
                continue
            seen.add(key)
            results.append(payload)
            if len(results) >= limit:
                break
        return results

    def _records(self, path: Path) -> list[IndexedExperience]:
        signature = _file_signature(path)
        cached = self._cache.get(path)
        if cached and cached[0] == signature:
            return cached[1]
        records: list[IndexedExperience] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            payload = json.loads(line)
            vector = vectorize_text(record_to_text(payload))
            if vector:
                records.append(IndexedExperience(payload=payload, vector=vector, order=index))
        self._cache[path] = (signature, records)
        return records


def record_to_text(payload: dict[str, Any]) -> str:
    weighted_parts: list[str] = []
    for key in [
        "task_id",
        "skill_id",
        "name",
        "failure_type",
        "reflection",
        "trigger_conditions",
        "applicable_future_tasks",
        "symptoms",
        "likely_causes",
        "recommended_recovery",
        "validated_on",
    ]:
        value = payload.get(key)
        if value:
            weighted_parts.extend([_flatten_text(value)] * _field_weight(key))
    weighted_parts.append(_flatten_text(payload))
    return "\n".join(weighted_parts)


def vectorize_text(text: str) -> dict[int, float]:
    weights: dict[int, float] = {}
    for feature, weight in extract_features(text):
        bucket = _bucket(feature)
        weights[bucket] = weights.get(bucket, 0.0) + weight
    norm = math.sqrt(sum(value * value for value in weights.values()))
    if norm == 0:
        return {}
    return {bucket: value / norm for bucket, value in weights.items()}


def extract_features(text: str) -> list[tuple[str, float]]:
    features: list[tuple[str, float]] = []
    for token in _tokens(text):
        expanded = _expand_token(token)
        for item in expanded:
            features.append((f"tok:{item}", 1.0))
        if "/" in token or token.endswith(".py"):
            for part in _path_parts(token):
                features.append((f"path:{part}", 1.4))
        for gram in _char_ngrams(token):
            features.append((f"tri:{gram}", 0.15))
    return features


def cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(bucket, 0.0) for bucket, value in left.items())


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z_][a-zA-Z0-9_./-]{1,}", text.lower())
    tokens: list[str] = []
    for token in raw:
        token = token.strip("._-/").replace("-", "_")
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
        for part in re.split(r"[._/-]+", token):
            if part and part not in STOPWORDS:
                tokens.append(part)
    return tokens


def _expand_token(token: str) -> set[str]:
    values = {token}
    if token.endswith("ies") and len(token) > 4:
        values.add(token[:-3] + "y")
    if token.endswith("s") and len(token) > 4:
        values.add(token[:-1])
    if token.endswith("ing") and len(token) > 5:
        values.add(token[:-3])
    if token.endswith("ed") and len(token) > 4:
        values.add(token[:-2])
    expanded = set(values)
    for value in values:
        expanded.update(ALIAS_TO_GROUP.get(value, set()))
    return expanded


def _path_parts(token: str) -> list[str]:
    parts = [part for part in re.split(r"[./_-]+", token) if part and part not in STOPWORDS]
    return [*parts, token]


def _char_ngrams(token: str) -> list[str]:
    compact = re.sub(r"[^a-z0-9_]", "", token)
    if len(compact) < 5:
        return []
    return [compact[index : index + 3] for index in range(len(compact) - 2)]


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items())
    return str(value)


def _field_weight(key: str) -> int:
    if key in {"task_id", "skill_id", "trigger_conditions", "applicable_future_tasks", "failure_type"}:
        return 3
    if key in {"reflection", "name", "recommended_recovery"}:
        return 2
    return 1


def _bucket(feature: str) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % VECTOR_DIMENSIONS


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _record_key(payload: dict[str, Any]) -> str:
    for key in ["skill_id", "task_id", "failure_type"]:
        value = payload.get(key)
        if value:
            return f"{key}:{value}"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
