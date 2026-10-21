from __future__ import annotations

try:
    from fastapi import FastAPI
except Exception:  # pragma: no cover - FastAPI is an optional runtime dependency in local tests.
    FastAPI = None

from app.api.tasks import attach_routes


def create_app():
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Install project dependencies to run the API.")
    application = FastAPI(title="PatchForge", version="0.1.0")
    attach_routes(application)
    return application


app = create_app() if FastAPI is not None else None

