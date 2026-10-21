from __future__ import annotations

import copy
import json
from enum import Enum
from typing import Any

try:  # Keep the public shape Pydantic-first, with a local fallback for broken envs.
    from pydantic import BaseModel as _PydanticBaseModel
    from pydantic import Field as Field

    class BaseSchema(_PydanticBaseModel):
        model_config = {"arbitrary_types_allowed": True}

except Exception:  # pragma: no cover - exercised only when pydantic is unavailable/broken.

    class _DefaultFactory:
        def __init__(self, factory: Any) -> None:
            self.factory = factory

    def Field(default: Any = None, default_factory: Any = None, **_: Any) -> Any:
        if default_factory is not None:
            return _DefaultFactory(default_factory)
        return default

    class BaseSchema:
        """Small subset of Pydantic's API for local/offline execution."""

        def __init__(self, **data: Any) -> None:
            annotations: dict[str, Any] = {}
            for cls in reversed(type(self).mro()):
                annotations.update(getattr(cls, "__annotations__", {}))
            for key in annotations:
                if key in data:
                    value = data.pop(key)
                elif hasattr(type(self), key):
                    default = getattr(type(self), key)
                    value = default.factory() if isinstance(default, _DefaultFactory) else copy.deepcopy(default)
                else:
                    value = None
                setattr(self, key, value)
            for key, value in data.items():
                setattr(self, key, value)

        @classmethod
        def model_validate(cls, value: Any) -> "BaseSchema":
            if isinstance(value, cls):
                return value
            if isinstance(value, dict):
                return cls(**value)
            raise TypeError(f"Cannot validate {type(value)!r} as {cls.__name__}")

        def model_dump(self, **_: Any) -> dict[str, Any]:
            def convert(value: Any) -> Any:
                if isinstance(value, BaseSchema):
                    return value.model_dump()
                if isinstance(value, Enum):
                    return value.value
                if isinstance(value, list):
                    return [convert(item) for item in value]
                if isinstance(value, dict):
                    return {key: convert(item) for key, item in value.items()}
                return value

            return {key: convert(value) for key, value in vars(self).items()}

        def model_dump_json(self, **kwargs: Any) -> str:
            return json.dumps(self.model_dump(), ensure_ascii=False, **kwargs)

        def dict(self, **kwargs: Any) -> dict[str, Any]:
            return self.model_dump(**kwargs)

