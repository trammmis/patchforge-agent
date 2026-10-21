from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.services.config import settings


class LLMError(RuntimeError):
    """Raised when a model provider cannot return a usable response."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass
class LLMResponse:
    provider: str
    model: str
    content: str
    raw: dict[str, Any]


class OfflineLLMClient:
    """Deterministic placeholder used when LLM mode is disabled."""

    provider = "offline"
    model = "offline-rule-agent"

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict[str, Any]:
        return {"schema": schema_name, "messages": len(messages), "mode": "offline", "diff": ""}


class DeepSeekLLMClient:
    """DeepSeek Chat Completions client using the OpenAI-compatible API surface."""

    provider = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        empty_retry_max_tokens: int = 8192,
        timeout_seconds: int = 90,
        request_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        fallback_model: str = "",
    ) -> None:
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model.strip()
        if self.fallback_model == self.model:
            self.fallback_model = ""
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.empty_retry_max_tokens = empty_retry_max_tokens
        self.timeout_seconds = timeout_seconds
        self.request_retries = max(0, request_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.last_request_duration_ms: int | None = None
        self.last_request_attempts = 0

    @classmethod
    def from_settings(cls) -> "DeepSeekLLMClient":
        return cls(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
            empty_retry_max_tokens=settings.deepseek_empty_retry_max_tokens,
            timeout_seconds=settings.deepseek_timeout_seconds,
            request_retries=settings.deepseek_request_retries,
            retry_backoff_seconds=settings.deepseek_retry_backoff_seconds,
            fallback_model=settings.deepseek_fallback_model,
        )

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict[str, Any]:
        response = self.chat(
            messages=messages,
            response_format={"type": "json_object"},
            metadata={"schema_name": schema_name},
        )
        try:
            payload = _parse_json_object(response.content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError(
                f"Model returned non-JSON content for {schema_name}",
                diagnostics={
                    "content_excerpt": response.content[:500],
                    "finish_reason": _first_finish_reason(response.raw),
                    "usage": response.raw.get("usage"),
                },
            ) from exc
        if not isinstance(payload, dict):
            raise LLMError(
                f"Model returned invalid JSON shape for {schema_name}",
                diagnostics={"payload_type": type(payload).__name__},
            )
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        if metadata:
            payload["metadata"] = metadata
        raw = self._post_chat_timed(payload)
        content, choice = _extract_message_content(raw)
        if _has_content(content) and _first_finish_reason(raw) != "length":
            return LLMResponse(provider=self.provider, model=self.model, content=content, raw=raw)

        diagnostics = _empty_content_diagnostics(raw, choice)
        fallback_payload = payload
        if _first_finish_reason(raw) == "length":
            retry_tokens = _retry_max_tokens(self.max_tokens, self.empty_retry_max_tokens)
            if retry_tokens is not None:
                retry_payload = {**payload, "max_tokens": retry_tokens}
                retry_raw = self._post_chat_timed(retry_payload)
                retry_content, retry_choice = _extract_message_content(retry_raw)
                if _has_content(retry_content):
                    return LLMResponse(provider=self.provider, model=self.model, content=retry_content, raw=retry_raw)
                retry_diagnostics = {
                    **_raw_response_diagnostics(retry_raw),
                    "choice_keys": sorted(retry_choice.keys()) if isinstance(retry_choice, dict) else [],
                    "message_keys": sorted((retry_choice.get("message") or {}).keys()) if isinstance(retry_choice, dict) else [],
                    "retry_max_tokens": retry_tokens,
                }
                diagnostics = {**diagnostics, "empty_retry": retry_diagnostics}
                fallback_payload = retry_payload

        fallback_response, fallback_diagnostics = self._try_fallback_model(fallback_payload)
        if fallback_response is not None:
            return fallback_response
        if fallback_diagnostics:
            diagnostics = {**diagnostics, "fallback": fallback_diagnostics}
        raise LLMError("DeepSeek API returned empty content", diagnostics=diagnostics)

    def _try_fallback_model(self, payload: dict[str, Any]) -> tuple[LLMResponse | None, dict[str, Any] | None]:
        if not self.fallback_model:
            return None, None
        fallback_payload = {**payload, "model": self.fallback_model}
        try:
            raw = self._post_chat_timed(fallback_payload)
            content, choice = _extract_message_content(raw)
        except LLMError as exc:
            return None, {
                "model": self.fallback_model,
                "error": str(exc),
                "diagnostics": exc.diagnostics,
            }
        if _has_content(content):
            return LLMResponse(provider=self.provider, model=self.fallback_model, content=content, raw=raw), None
        return None, {
            "model": self.fallback_model,
            **_empty_content_diagnostics(raw, choice),
        }

    def _post_chat_timed(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return self._post_chat(payload)
        finally:
            self.last_request_duration_ms = int((time.perf_counter() - started) * 1000)

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        attempts = self.request_retries + 1
        self.last_request_attempts = 0
        last_url_error: urllib.error.URLError | None = None
        last_http_error: urllib.error.HTTPError | None = None
        for attempt in range(1, attempts + 1):
            self.last_request_attempts = attempt
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_http_error = exc
                if not _is_transient_http_status(exc.code) or attempt >= attempts:
                    body = exc.read().decode("utf-8", errors="replace")
                    raise LLMError(f"DeepSeek API HTTP {exc.code}: {body[:500]}") from exc
                _sleep_before_retry(attempt, self.retry_backoff_seconds)
            except urllib.error.URLError as exc:
                last_url_error = exc
                if attempt >= attempts:
                    raise LLMError(f"DeepSeek API request failed after {attempts} attempts: {exc.reason}") from exc
                _sleep_before_retry(attempt, self.retry_backoff_seconds)
        if last_http_error is not None:
            body = last_http_error.read().decode("utf-8", errors="replace")
            raise LLMError(f"DeepSeek API HTTP {last_http_error.code}: {body[:500]}") from last_http_error
        if last_url_error is not None:
            raise LLMError(f"DeepSeek API request failed after {attempts} attempts: {last_url_error.reason}") from last_url_error
        raise LLMError("DeepSeek API request failed")


def build_llm_client() -> OfflineLLMClient | DeepSeekLLMClient:
    if settings.llm_provider == "deepseek":
        return DeepSeekLLMClient.from_settings()
    return OfflineLLMClient()


def _extract_message_content(raw: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    try:
        choice = raw["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(
            "DeepSeek API response did not include choices[0].message.content",
            diagnostics=_raw_response_diagnostics(raw),
        ) from exc
    return content, choice


def _parse_json_object(content: Any) -> dict[str, Any]:
    """Parse a JSON object even when the model adds a fence or trailing note."""

    if not isinstance(content, str):
        raise TypeError(f"expected string content, got {type(content).__name__}")
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("JSON object start not found")
    payload, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object, got {type(payload).__name__}")
    return payload


def _has_content(content: Any) -> bool:
    return isinstance(content, str) and bool(content.strip())


def _retry_max_tokens(current: int, retry_limit: int) -> int | None:
    if retry_limit <= current:
        return None
    return min(max(current * 2, current + 2048), retry_limit)


def _is_transient_http_status(code: int) -> bool:
    return code == 429 or 500 <= code < 600


def _sleep_before_retry(attempt: int, backoff_seconds: float) -> None:
    if backoff_seconds <= 0:
        return
    time.sleep(backoff_seconds * (2 ** (attempt - 1)))


def _empty_content_diagnostics(raw: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
    return {
        **_raw_response_diagnostics(raw),
        "choice_keys": sorted(choice.keys()) if isinstance(choice, dict) else [],
        "message_keys": sorted((choice.get("message") or {}).keys()) if isinstance(choice, dict) else [],
    }


def _first_finish_reason(raw: dict[str, Any]) -> str | None:
    try:
        return raw["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _raw_response_diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices") if isinstance(raw, dict) else None
    first_choice = choices[0] if isinstance(choices, list) and choices else {}
    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    return {
        "response_keys": sorted(raw.keys()) if isinstance(raw, dict) else [],
        "finish_reason": first_choice.get("finish_reason") if isinstance(first_choice, dict) else None,
        "usage": raw.get("usage") if isinstance(raw, dict) else None,
        "content_length": len(content) if isinstance(content, str) else None,
    }
