from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class TraceRecorder:
    def __init__(self, trace_path: Path | str) -> None:
        self.trace_path = Path(trace_path)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "type": event_type,
            "payload": payload,
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def latest_payload(self, event_type: str) -> dict[str, Any] | None:
        if not self.trace_path.exists():
            return None
        latest: dict[str, Any] | None = None
        for line in self.trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == event_type and isinstance(event.get("payload"), dict):
                latest = event["payload"]
        return latest

    def count(self, event_type: str) -> int:
        """Count recorded events without exposing the trace file to the Agent."""

        if not self.trace_path.exists():
            return 0
        count = 0
        for line in self.trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == event_type:
                count += 1
        return count
