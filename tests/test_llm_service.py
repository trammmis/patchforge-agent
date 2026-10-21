from __future__ import annotations

import json
import urllib.error
import unittest
from unittest.mock import patch

from app.services.llm import DeepSeekLLMClient


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DeepSeekLLMClientTests(unittest.TestCase):
    def test_retries_empty_length_response_with_larger_token_budget(self) -> None:
        calls: list[dict] = []
        responses = [
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "used all output tokens"},
                    }
                ],
                "usage": {"completion_tokens": 4096},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok": true}'},
                    }
                ],
                "usage": {"completion_tokens": 12},
            },
        ]

        def fake_urlopen(request, timeout=90):  # noqa: ANN001
            calls.append(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse(responses.pop(0))

        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            max_tokens=4096,
            empty_retry_max_tokens=8192,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual([call["max_tokens"] for call in calls], [4096, 8192])

    def test_retries_truncated_nonempty_response_with_larger_token_budget(self) -> None:
        calls: list[dict] = []
        responses = [
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"ok": true'},
                    }
                ],
                "usage": {"completion_tokens": 4096},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok": true}'},
                    }
                ],
                "usage": {"completion_tokens": 12},
            },
        ]

        def fake_urlopen(request, timeout=90):  # noqa: ANN001
            calls.append(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse(responses.pop(0))

        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            max_tokens=4096,
            empty_retry_max_tokens=8192,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual([call["max_tokens"] for call in calls], [4096, 8192])

    def test_accepts_json_object_with_model_wrapper_text(self) -> None:
        payload = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "```json\n{\"ok\": true}\n```\nDone."},
                }
            ]
        }
        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            request_retries=0,
        )

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True})

    def test_falls_back_to_stable_model_after_empty_content(self) -> None:
        calls: list[dict] = []
        responses = [
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "used all output tokens"},
                    }
                ],
                "usage": {"completion_tokens": 4096},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "", "reasoning_content": "still no final answer"},
                    }
                ],
                "usage": {"completion_tokens": 8192},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok": true, "source": "fallback"}'},
                    }
                ],
                "usage": {"completion_tokens": 24},
            },
        ]

        def fake_urlopen(request, timeout=90):  # noqa: ANN001
            calls.append(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse(responses.pop(0))

        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-v4-flash",
            fallback_model="deepseek-chat",
            max_tokens=4096,
            empty_retry_max_tokens=8192,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True, "source": "fallback"})
        self.assertEqual([call["model"] for call in calls], ["deepseek-v4-flash", "deepseek-v4-flash", "deepseek-chat"])
        self.assertEqual([call["max_tokens"] for call in calls], [4096, 8192, 8192])

    def test_retries_transient_url_errors(self) -> None:
        calls: list[dict] = []
        failures = [urllib.error.URLError("[SSL: UNEXPECTED_EOF_WHILE_READING]"), urllib.error.URLError("timed out")]

        def fake_urlopen(request, timeout=90):  # noqa: ANN001
            calls.append(json.loads(request.data.decode("utf-8")))
            if failures:
                raise failures.pop(0)
            return _FakeHTTPResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"ok": true}'},
                        }
                    ],
                    "usage": {"completion_tokens": 12},
                }
            )

        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            request_retries=2,
            retry_backoff_seconds=0,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_reports_url_error_after_retry_budget_is_exhausted(self) -> None:
        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            request_retries=1,
            retry_backoff_seconds=0,
        )

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaisesRegex(Exception, "after 2 attempts"):
                client.complete_json(
                    messages=[{"role": "user", "content": "return json"}],
                    schema_name="unit_test",
                )

    def test_records_request_duration_and_attempt_count(self) -> None:
        payload = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true}'},
                }
            ]
        }
        client = DeepSeekLLMClient(
            api_key="test-key",
            base_url="https://api.example.test",
            model="deepseek-test",
            request_retries=0,
        )

        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            result = client.complete_json(
                messages=[{"role": "user", "content": "return json"}],
                schema_name="unit_test",
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.last_request_attempts, 1)
        self.assertIsNotNone(client.last_request_duration_ms)
        self.assertGreaterEqual(client.last_request_duration_ms or 0, 0)


if __name__ == "__main__":
    unittest.main()
