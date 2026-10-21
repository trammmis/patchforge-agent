from __future__ import annotations

from pathlib import Path


def load_config(path: str | Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config

