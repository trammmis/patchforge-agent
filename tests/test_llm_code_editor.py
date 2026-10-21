from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.code_editor import CodeEditor
from app.agents.code_editor import build_patch_prompt
from app.agents.code_editor import build_diff_from_range_edits
from app.agents.code_editor import build_range_edit_prompt
from app.agents.code_editor import check_llm_patch_quality
from app.agents.code_editor import extract_range_edits
from app.agents.code_editor import line_numbered_content
from app.agents.code_editor import range_edit_context
from app.agents.code_editor import retry_context
from app.agents.code_editor import validate_unified_diff_shape
from app.agents.code_editor import _repeated_failed_patch_change
from app.agents.code_editor import _add_mro_own_only_branch
from app.agents.code_editor import _summarize_llm_failure_for_retry
from app.harness.sandbox_runner import SandboxRunner
from app.harness.tool_runtime import ToolRuntime
from app.harness.trace_recorder import TraceRecorder
from app.services.llm import LLMError, LLMResponse


class _FakeLLMClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, diff: str) -> None:
        self.diff = diff

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        return {"diff": self.diff, "notes": f"schema={schema_name}"}


class _FlakyLLMClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, diff: str) -> None:
        self.diff = diff
        self.calls = 0

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise LLMError("DeepSeek API returned empty content")
        self.last_prompt = messages[-1]["content"]
        return {"diff": self.diff, "notes": f"schema={schema_name}"}


class _JsonEmptyThenDirectDiffClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, diff: str, direct_response: str | None = None) -> None:
        self.diff = diff
        self.direct_response = direct_response if direct_response is not None else diff
        self.json_calls = 0
        self.chat_calls = 0
        self.last_direct_prompt = ""

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        self.json_calls += 1
        raise LLMError(
            "DeepSeek API returned empty content",
            diagnostics={"finish_reason": "stop", "usage": {"prompt_tokens": 100}},
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        self.last_direct_prompt = messages[-1]["content"]
        return LLMResponse(provider=self.provider, model=self.model, content=self.direct_response, raw={})


class _BadJsonPatchThenDirectDiffClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, bad_diff: str, direct_diff: str) -> None:
        self.bad_diff = bad_diff
        self.direct_diff = direct_diff
        self.json_calls = 0
        self.chat_calls = 0

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        self.json_calls += 1
        return {"diff": self.bad_diff, "notes": f"schema={schema_name}"}

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        return LLMResponse(provider=self.provider, model=self.model, content=self.direct_diff, raw={})


class _JsonEmptyBadDirectThenRangeClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, range_edits: list[dict]) -> None:
        self.range_edits = range_edits
        self.patch_json_calls = 0
        self.range_calls = 0
        self.chat_calls = 0
        self.last_range_prompt = ""

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        if schema_name == "range_patch_generation":
            self.range_calls += 1
            self.last_range_prompt = messages[-1]["content"]
            return {"edits": self.range_edits, "notes": "range fallback"}
        self.patch_json_calls += 1
        raise LLMError("DeepSeek API returned empty content")

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        return LLMResponse(provider=self.provider, model=self.model, content="not a diff", raw={})


class _JsonEmptyBadDirectThenMicroClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, micro_response: str) -> None:
        self.micro_response = micro_response
        self.patch_json_calls = 0
        self.chat_calls = 0
        self.last_micro_prompt = ""

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        self.patch_json_calls += 1
        raise LLMError("DeepSeek API returned empty content")

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        schema_name = (metadata or {}).get("schema_name")
        if schema_name == "patch_generation_micro_diff":
            self.last_micro_prompt = messages[-1]["content"]
            return LLMResponse(provider=self.provider, model=self.model, content=self.micro_response, raw={})
        return LLMResponse(provider=self.provider, model=self.model, content="not a diff", raw={})


class _PreflightThenDirectApiThenRangeClient:
    provider = "deepseek"
    model = "fake-model"

    def __init__(self, bad_diff: str, range_response: dict) -> None:
        self.bad_diff = bad_diff
        self.range_response = range_response
        self.patch_json_calls = 0
        self.range_calls = 0
        self.chat_calls = 0
        self.last_range_prompt = ""

    def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
        if schema_name == "range_patch_generation":
            self.range_calls += 1
            self.last_range_prompt = messages[-1]["content"]
            return self.range_response
        self.patch_json_calls += 1
        if self.patch_json_calls == 1:
            raise LLMError("DeepSeek API returned empty content")
        return {"diff": self.bad_diff, "notes": f"schema={schema_name}"}

    def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> LLMResponse:
        self.chat_calls += 1
        raise LLMError("DeepSeek API request failed: [SSL: UNEXPECTED_EOF_WHILE_READING]")


class LLMCodeEditorTests(unittest.TestCase):
    def test_deepseek_path_accepts_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_demo_file(Path(tmp))
            diff = (
                "--- a/src/patchforge_demo/calculator.py\n"
                "+++ b/src/patchforge_demo/calculator.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def f():\n"
                "-    return None\n"
                "+    return 0\n"
            )
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=_FakeLLMClient(diff)
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix unknown issue",
                    candidate_files=["src/patchforge_demo/calculator.py"],
                    plan=["patch"],
                )
            self.assertEqual(result, diff)

    def test_deepseek_path_retries_with_compact_prompt_after_empty_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def skipping():\n"
                "-    pass\n"
                "+    return None\n"
            )
            client = _FlakyLLMClient(diff)
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=[
                        "src/_pytest/skipping.py",
                        "testing/test_skipping.py",
                        "src/_pytest/cacheprovider.py",
                        "src/_pytest/doctest.py",
                    ],
                    plan=["patch"],
                )

            self.assertEqual(result, diff)
            self.assertEqual(client.calls, 2)
            self.assertIn("PROMPT_MODE: compact retry", client.last_prompt)

    def test_deepseek_path_uses_direct_diff_fallback_after_json_empty_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            diff = (
                "Some preface that should be ignored.\n"
                "```diff\n"
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def skipping():\n"
                "-    pass\n"
                "+    return None\n"
                "```\n"
            )
            client = _JsonEmptyThenDirectDiffClient(
                diff="",
                direct_response=diff,
            )
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=[
                        "src/_pytest/skipping.py",
                        "testing/test_skipping.py",
                        "src/_pytest/cacheprovider.py",
                    ],
                    plan=["patch"],
                )

            self.assertTrue(result.startswith("--- a/src/_pytest/skipping.py"))
            self.assertEqual(client.json_calls, 2)
            self.assertEqual(client.chat_calls, 1)
            self.assertIn("PROMPT_MODE: direct diff fallback", client.last_direct_prompt)

    def test_deepseek_path_records_failure_type_when_all_generation_modes_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            client = _JsonEmptyThenDirectDiffClient(diff="", direct_response="not a diff")
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertEqual(result, "")
            failure = runtime.trace.latest_payload("patch_generation_failed")
            self.assertIsNotNone(failure)
            self.assertEqual(failure["failure_type"], "llm_empty_response")

    def test_preflight_rejects_context_mismatch_and_uses_direct_diff_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            bad_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def not_the_current_function():\n"
                "-    pass\n"
                "+    return None\n"
            )
            direct_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def skipping():\n"
                "-    pass\n"
                "+    return None\n"
            )
            client = _BadJsonPatchThenDirectDiffClient(bad_diff, direct_diff)

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertEqual(result, direct_diff)
            self.assertEqual(client.json_calls, 2)
            self.assertEqual(client.chat_calls, 1)
            trace_events = [json.loads(line) for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(any(item["failure_type"] == "llm_patch_preflight_failed" for item in failures))

    def test_preflight_rejects_python_syntax_error_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            bad_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,3 @@\n"
                " def skipping():\n"
                "     pass\n"
                "+    if\n"
            )
            direct_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def skipping():\n"
                "-    pass\n"
                "+    return None\n"
            )
            client = _BadJsonPatchThenDirectDiffClient(bad_diff, direct_diff)

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertEqual(result, direct_diff)
            trace_events = [json.loads(line) for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(any(item["failure_type"] == "llm_patch_syntax_failed" for item in failures))

    def test_patch_quality_rejects_broad_source_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            content = (
                "def one():\n"
                "    return 1\n"
                "\n"
                "def two():\n"
                "    return 2\n"
                "\n"
                "def three():\n"
                "    return 3\n"
                "\n"
                "def four():\n"
                "    return 4\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "skipping.py").write_text(content, encoding="utf-8")
            broad_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def one():\n"
                "-    return 1\n"
                "+    return 10\n"
                "@@ -4,2 +4,2 @@\n"
                " def two():\n"
                "-    return 2\n"
                "+    return 20\n"
                "@@ -7,2 +7,2 @@\n"
                " def three():\n"
                "-    return 3\n"
                "+    return 30\n"
                "@@ -10,2 +10,2 @@\n"
                " def four():\n"
                "-    return 4\n"
                "+    return 40\n"
            )

            result = check_llm_patch_quality(runtime.workspace, broad_diff)

            self.assertIsNotNone(result)
            self.assertEqual(result["failure_type"], "too_many_source_hunks")

    def test_patch_quality_rejects_hashing_mark_args_for_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(
                    "def get_unpacked_marks(obj):\n"
                    "    mark_list = getattr(obj, \"pytestmark\", [])\n"
                    "    return normalize_mark_list(mark_list)\n",
                    encoding="utf-8",
                )
            diff = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,3 +1,12 @@\n"
                " def get_unpacked_marks(obj):\n"
                "+    seen = set()\n"
                "+    marks = []\n"
                "+    for mark in normalize_mark_list(getattr(obj, \"pytestmark\", [])):\n"
                "+        key = (mark.name, mark.args, tuple(sorted(mark.kwargs.items())))\n"
                "+        if key not in seen:\n"
                "+            seen.add(key)\n"
                "+            marks.append(mark)\n"
                "+    return marks\n"
                "     mark_list = getattr(obj, \"pytestmark\", [])\n"
                "     return normalize_mark_list(mark_list)\n"
            )

            result = check_llm_patch_quality(runtime.workspace, diff)

            self.assertIsNotNone(result)
            self.assertEqual(result["failure_type"], "risky_mark_deduplication")

    def test_patch_quality_rejects_deduplicating_marks_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(
                    "def get_unpacked_marks(obj):\n"
                    "    mark_list = getattr(obj, \"pytestmark\", [])\n"
                    "    return normalize_mark_list(mark_list)\n",
                    encoding="utf-8",
                )
            diff = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,3 +1,12 @@\n"
                " def get_unpacked_marks(obj):\n"
                "+    seen = set()\n"
                "+    marks = []\n"
                "+    for mark in normalize_mark_list(getattr(obj, \"pytestmark\", [])):\n"
                "+        if mark.name not in seen:\n"
                "+            seen.add(mark.name)\n"
                "+            marks.append(mark)\n"
                "+    return marks\n"
                "     mark_list = getattr(obj, \"pytestmark\", [])\n"
                "     return normalize_mark_list(mark_list)\n"
            )

            result = check_llm_patch_quality(runtime.workspace, diff)

            self.assertIsNotNone(result)
            self.assertEqual(result["failure_type"], "risky_mark_name_deduplication")

    def test_patch_quality_rejects_docstring_only_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = (
                "class Marker:\n"
                "    \"\"\"Usage notes.\n"
                "\n"
                "    1. Existing behavior.\n"
                "    2. More documentation.\n"
                "    \"\"\"\n"
                "\n"
                "    def get(self):\n"
                "        return []\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(original, encoding="utf-8")
            diff = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -2,5 +2,8 @@\n"
                "     \"\"\"Usage notes.\n"
                " \n"
                "-    1. Existing behavior.\n"
                "-    2. More documentation.\n"
                "+    marks = []\n"
                "+    for cls in obj.__mro__:\n"
                "+        marks.extend(cls.__dict__.get(\"pytestmark\", []))\n"
                "+    return marks\n"
                "     \"\"\"\n"
            )

            result = check_llm_patch_quality(runtime.workspace, diff)

            self.assertIsNotNone(result)
            self.assertEqual(result["failure_type"], "docstring_only_source_change")

    def test_preflight_rejects_repeated_bare_generator_return_after_list_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = (
                "def get_unpacked_marks(obj):\n"
                "    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "    return normalize_mark_list(mark_list)\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(original, encoding="utf-8")

            bare_generator_diff = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,3 +1,5 @@\n"
                " def get_unpacked_marks(obj):\n"
                "-    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "-    return normalize_mark_list(mark_list)\n"
                "+    marks = []\n"
                "+    for cls in obj.__mro__:\n"
                "+        marks.extend(cls.__dict__.get(\"pytestmark\", []))\n"
                "+    return normalize_mark_list(marks)\n"
            )
            concrete_list_diff = bare_generator_diff.replace(
                "return normalize_mark_list(marks)",
                "return list(normalize_mark_list(marks))",
            )
            client = _BadJsonPatchThenDirectDiffClient(bare_generator_diff, concrete_list_diff)

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix mark MRO collection",
                    candidate_files=["src/_pytest/mark/structures.py"],
                    plan=["collect class marks"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "failure_excerpt": (
                                "assert <generator object normalize_mark_list at 0x1> == [Mark(...)] "
                                "when the result is compared to a list"
                            ),
                        }
                    ],
                    expected_modified_files=["src/_pytest/mark/structures.py"],
                )

            self.assertIn("return list(normalize_mark_list(marks))", result)
            trace_events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(
                any(
                    item["failure_type"] == "llm_patch_quality_failed"
                    and item.get("diagnostics", {}).get("quality_check", {}).get("failure_type")
                    == "bare_generator_return_after_list_failure"
                    for item in failures
                )
            )

    def test_generator_shape_repair_reuses_previous_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = (
                "def get_unpacked_marks(obj):\n"
                "    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "    return normalize_mark_list(mark_list)\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(original, encoding="utf-8")
            failed_diff = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,3 +1,4 @@\n"
                " def get_unpacked_marks(obj):\n"
                "     mark_list = getattr(obj, \"pytestmark\", [])\n"
                "-    return normalize_mark_list(mark_list)\n"
                "+    return normalize_mark_list(marks)\n"
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix the MRO marker return shape",
                    candidate_files=["src/_pytest/mark/structures.py"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "failure_excerpt": (
                                "assert <generator object normalize_mark_list at 0x1> == [Mark(...)]"
                            ),
                            "diff_excerpt": failed_diff,
                        }
                    ],
                    expected_modified_files=["src/_pytest/mark/structures.py"],
                )

            self.assertIn("return list(normalize_mark_list(marks))", result)
            events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(
                any(
                    event["type"] == "semantic_repair_generation"
                    and event["payload"].get("success") is True
                    for event in events
                )
            )

    def test_mro_storage_repair_uses_own_dictionary_for_false_flag(self) -> None:
        failed_diff = (
            "--- a/src/_pytest/mark/structures.py\n"
            "+++ b/src/_pytest/mark/structures.py\n"
            "@@ -1,8 +1,15 @@\n"
            " def get_unpacked_marks(obj):\n"
            "+    if consider_mro and inspect.isclass(obj):\n"
            "+        for cls in obj.__mro__:\n"
            "+            pass\n"
            "     mark_list = getattr(obj, \"pytestmark\", [])\n"
            "     return normalize_mark_list(mark_list)\n"
            "@@ -10,2 +17,2 @@\n"
            "-    obj.pytestmark = [*get_unpacked_marks(obj), mark]\n"
            "+    obj.pytestmark = [*get_unpacked_marks(obj, consider_mro=False), mark]\n"
        )

        repaired = _add_mro_own_only_branch(failed_diff)

        self.assertIn(
            'mark_list = obj.__dict__.get("pytestmark", []) if inspect.isclass(obj) and not consider_mro',
            repaired,
        )
        self.assertIn("-    mark_list = getattr(obj, \"pytestmark\", [])", repaired)

    def test_preflight_rejects_mark_mro_retry_without_own_storage_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = (
                "def get_unpacked_marks(obj):\n"
                "    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "    return normalize_mark_list(mark_list)\n"
                "\n"
                "def store_mark(obj, mark):\n"
                "    obj.pytestmark = [*get_unpacked_marks(obj), mark]\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "mark").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "mark" / "structures.py").write_text(original, encoding="utf-8")

            missing_store_fix = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,6 +1,12 @@\n"
                " def get_unpacked_marks(obj):\n"
                "-    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "-    return normalize_mark_list(mark_list)\n"
                "+    marks = []\n"
                "+    for cls in obj.__mro__:\n"
                "+        marks.extend(cls.__dict__.get(\"pytestmark\", []))\n"
                "+    return marks\n"
                " \n"
                " def store_mark(obj, mark):\n"
                "     obj.pytestmark = [*get_unpacked_marks(obj), mark]\n"
            )
            with_store_fix = (
                "--- a/src/_pytest/mark/structures.py\n"
                "+++ b/src/_pytest/mark/structures.py\n"
                "@@ -1,6 +1,15 @@\n"
                "-def get_unpacked_marks(obj):\n"
                "+def get_unpacked_marks(obj, *, consider_mro=True):\n"
                "-    mark_list = getattr(obj, \"pytestmark\", [])\n"
                "-    return normalize_mark_list(mark_list)\n"
                "+    objects = obj.__mro__ if consider_mro else (obj,)\n"
                "+    marks = []\n"
                "+    for cls in objects:\n"
                "+        marks.extend(cls.__dict__.get(\"pytestmark\", []))\n"
                "+    return marks\n"
                " \n"
                " def store_mark(obj, mark):\n"
                "-    obj.pytestmark = [*get_unpacked_marks(obj), mark]\n"
                "+    obj.pytestmark = [*get_unpacked_marks(obj, consider_mro=False), mark]\n"
            )
            client = _BadJsonPatchThenDirectDiffClient(missing_store_fix, with_store_fix)

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix mark MRO collection",
                    candidate_files=["src/_pytest/mark/structures.py"],
                    plan=["collect marks and preserve storage"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "failed_tests": ["testing/test_mark.py::test_mark_mro"],
                            "failure_excerpt": (
                                "At index 0 diff: Mark args=('a',) != Mark args=('c',). "
                                "Left contains 2 more items."
                            ),
                            "preservation_hints": ["base marks are not copied twice"],
                        }
                    ],
                    expected_modified_files=["src/_pytest/mark/structures.py"],
                )

            self.assertIn("consider_mro=False", result)
            trace_events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(
                any(
                    item["failure_type"] == "llm_patch_quality_failed"
                    and item.get("diagnostics", {}).get("quality_check", {}).get("failure_type")
                    == "missing_mark_mro_own_storage_flag"
                    for item in failures
                )
            )

    def test_preflight_rejects_broad_quality_patch_and_uses_direct_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            content = (
                "def one():\n"
                "    return 1\n"
                "\n"
                "def two():\n"
                "    return 2\n"
                "\n"
                "def three():\n"
                "    return 3\n"
                "\n"
                "def four():\n"
                "    return 4\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "skipping.py").write_text(content, encoding="utf-8")
            broad_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def one():\n"
                "-    return 1\n"
                "+    return 10\n"
                "@@ -4,2 +4,2 @@\n"
                " def two():\n"
                "-    return 2\n"
                "+    return 20\n"
                "@@ -7,2 +7,2 @@\n"
                " def three():\n"
                "-    return 3\n"
                "+    return 30\n"
                "@@ -10,2 +10,2 @@\n"
                " def four():\n"
                "-    return 4\n"
                "+    return 40\n"
            )
            focused_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def one():\n"
                "-    return 1\n"
                "+    return 10\n"
            )
            client = _BadJsonPatchThenDirectDiffClient(broad_diff, focused_diff)

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertEqual(result, focused_diff)
            trace_events = [json.loads(line) for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(any(item["failure_type"] == "llm_patch_quality_failed" for item in failures))

    def test_range_edit_fallback_generates_checked_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            client = _JsonEmptyBadDirectThenRangeClient(
                [
                    {
                        "path": "src/_pytest/skipping.py",
                        "start_line": 2,
                        "end_line": 2,
                        "new_content": "    return None",
                    }
                ]
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertIn("--- a/src/_pytest/skipping.py", result)
            self.assertIn("+    return None", result)
            self.assertEqual(client.patch_json_calls, 2)
            self.assertEqual(client.chat_calls, 1)
            self.assertEqual(client.range_calls, 1)
            self.assertIn("LINE_NUMBERED_REPOSITORY_CONTEXT", client.last_range_prompt)

    def test_micro_diff_fallback_uses_failure_feedback_before_range_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = "def collect_file(path):\n    return False\n"
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "python.py").write_text(original, encoding="utf-8")
            client = _JsonEmptyBadDirectThenMicroClient(
                '{"edits":[{"path":"src/_pytest/python.py","start_line":2,"end_line":2,"new_content":"    return path.basename == \\"__init__.py\\""}]}'
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix package collection after a failed test",
                    candidate_files=["src/_pytest/python.py"],
                    plan=["preserve explicit package files"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "failed_tests": ["testing/test_collection.py::Test_getinitialnodes::test_pkgfile"],
                            "preservation_hints": ["Preserve explicit __init__.py package collection."],
                        }
                    ],
                    expected_modified_files=["src/_pytest/python.py"],
                )

            self.assertIn("--- a/src/_pytest/python.py", result)
            self.assertIn('+    return path.basename == "__init__.py"', result)
            self.assertEqual(client.patch_json_calls, 2)
            self.assertEqual(client.chat_calls, 2)
            self.assertIn("PROMPT_MODE: micro diff fallback", client.last_micro_prompt)
            trace_events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            successes = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and event["payload"].get("success")
            ]
            self.assertTrue(any(item["prompt_mode"] == "micro_diff" for item in successes))

    def test_fast_repair_uses_one_primary_request_before_micro_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = "def collect_file(path):\n    return False\n"
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "python.py").write_text(original, encoding="utf-8")
            runtime.trace.record(
                "llm_patch_generation",
                {"success": False, "failure_type": "llm_patch_preflight_failed"},
            )
            client = _JsonEmptyBadDirectThenMicroClient(
                '{"edits":[{"path":"src/_pytest/python.py","start_line":2,"end_line":2,"new_content":"    return path.basename == \\\"__init__.py\\\""}]}'
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.settings.deepseek_fast_repair", True
            ), patch("app.agents.code_editor.build_llm_client", return_value=client):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix package collection after a failed test",
                    candidate_files=["src/_pytest/python.py"],
                    plan=["preserve explicit package files"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "failed_tests": ["testing/test_collection.py::test_pkgfile"],
                            "preservation_hints": ["Preserve explicit __init__.py package collection."],
                        }
                    ],
                    expected_modified_files=["src/_pytest/python.py"],
                )

            self.assertIn("--- a/src/_pytest/python.py", result)
            self.assertEqual(client.patch_json_calls, 1)
            self.assertEqual(client.chat_calls, 1)

    def test_range_edit_uses_preflight_error_even_after_direct_api_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            bad_diff = (
                "--- a/src/_pytest/skipping.py\n"
                "+++ b/src/_pytest/skipping.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def not_the_current_function():\n"
                "-    pass\n"
                "+    return None\n"
            )
            client = _PreflightThenDirectApiThenRangeClient(
                bad_diff,
                {
                    "changes": [
                        {
                            "path": "src/_pytest/skipping.py",
                            "start_line": 2,
                            "end_line": 2,
                            "new_content": "    return None",
                        }
                    ]
                },
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix pytest skip location",
                    candidate_files=["src/_pytest/skipping.py", "testing/test_skipping.py"],
                    plan=["patch"],
                )

            self.assertIn("+    return None", result)
            self.assertEqual(client.patch_json_calls, 2)
            self.assertEqual(client.chat_calls, 1)
            self.assertEqual(client.range_calls, 1)
            self.assertIn("Patch context mismatch", client.last_range_prompt)
            self.assertIn("diff_excerpt", client.last_range_prompt)

    def test_extract_range_edits_accepts_nested_aliases(self) -> None:
        edits = extract_range_edits(
            {
                "files": [
                    {
                        "file_path": "src/app.py",
                        "edits": [
                            {
                                "start": 3,
                                "end": 4,
                                "replacement": "    return 1",
                            }
                        ],
                    }
                ]
            }
        )

        self.assertEqual(edits[0]["path"], "src/app.py")
        self.assertEqual(edits[0]["start_line"], 3)
        self.assertEqual(edits[0]["end_line"], 4)
        self.assertEqual(edits[0]["new_content"], "    return 1")

    def test_rejects_placeholder_hunk_header_before_apply(self) -> None:
        malformed = (
            "--- a/src/_pytest/skipping.py\n"
            "+++ b/src/_pytest/skipping.py\n"
            "@@ -XXX,XX +XXX,XX @@\n"
            "-    pass\n"
            "+    return None\n"
        )

        error = validate_unified_diff_shape(malformed)

        self.assertIsNotNone(error)
        self.assertIn("malformed diff hunk header", error or "")

    def test_line_numbered_content_and_range_edit_diff_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))

            numbered = line_numbered_content("a\nb\n")
            diff = build_diff_from_range_edits(
                runtime,
                [
                    {
                        "path": "src/_pytest/skipping.py",
                        "start_line": 2,
                        "end_line": 2,
                        "new_content": "    return None",
                    }
                ],
            )

            self.assertIn("0001 | a", numbered)
            self.assertIn("0002 | b", numbered)
            self.assertIn("-    pass", diff)
            self.assertIn("+    return None", diff)

    def test_range_edit_prompt_requests_inclusive_line_ranges(self) -> None:
        prompt = build_range_edit_prompt(
            issue_text="Fix bug",
            context=[{"path": "src/app.py", "kind": "source", "priority": "primary", "content": "def f():\n    pass\n"}],
            plan=["patch"],
            test_results=[],
            patch_failures=[],
            previous_error="Patch context mismatch",
        )

        self.assertIn("0001 | def f():", prompt)
        self.assertIn("start_line and end_line are 1-based and inclusive", prompt)
        self.assertIn("current LINE_NUMBERED_REPOSITORY_CONTEXT exactly", prompt)
        self.assertIn("repair that exact failed edit location", prompt)
        self.assertIn("new_content", prompt)

    def test_range_edit_context_reloads_full_primary_source_from_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            marker = "def target_fix_location_for_range_edit():"
            long_source = (
                "".join(f"# filler {idx:04d} {'x' * 42}\n" for idx in range(230))
                + f"{marker}\n"
                + "    return 'visible only after reload'\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "skipping.py").write_text(long_source, encoding="utf-8")

            context = CodeEditor()._build_context(
                runtime,
                ["src/_pytest/skipping.py", "testing/test_skipping.py"],
                [],
            )
            expanded = range_edit_context(context, runtime=runtime)

            self.assertNotIn(marker, context[0]["content"])
            self.assertIn(marker, expanded[0]["content"])

    def test_failure_retry_summary_includes_syntax_and_quality_checks(self) -> None:
        summary = _summarize_llm_failure_for_retry(
            {
                "prompt_mode": "range_edit",
                "failure_type": "llm_patch_syntax_failed",
                "error": "Generated patch failed Python syntax preflight",
                "diagnostics": {
                    "syntax_check": {
                        "file": "src/_pytest/skipping.py",
                        "location": "src/_pytest/skipping.py:195:8",
                        "message": "IndentationError: unexpected indent",
                    },
                    "quality_check": {
                        "failure_type": "too_many_source_hunks",
                        "message": "too broad",
                    },
                    "diff_excerpt": "@@ -192,7 +192,7 @@",
                },
            }
        )

        self.assertIn("syntax_check", summary)
        self.assertIn("src/_pytest/skipping.py:195:8", summary)
        self.assertIn("quality_check", summary)

    def test_patch_no_effect_failures_do_not_frontload_wrong_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "testing" / "acceptance_test.py").write_text("def test_cli():\n    pass\n", encoding="utf-8")

            context = CodeEditor()._build_context(
                runtime,
                [
                    "src/_pytest/skipping.py",
                    "testing/test_skipping.py",
                    "testing/acceptance_test.py",
                ],
                [
                    {
                        "failure_type": "patch_no_effect",
                        "changed_files": ["testing/acceptance_test.py"],
                    }
                ],
            )

            self.assertEqual([item["path"] for item in context[:2]], ["src/_pytest/skipping.py", "testing/test_skipping.py"])

    def test_patch_apply_failures_keep_primary_source_and_test_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "testing" / "acceptance_test.py").write_text("def test_cli():\n    pass\n", encoding="utf-8")

            context = CodeEditor()._build_context(
                runtime,
                [
                    "src/_pytest/skipping.py",
                    "testing/test_skipping.py",
                    "testing/acceptance_test.py",
                ],
                [
                    {
                        "failure_type": "patch_apply_failed",
                        "changed_files": ["testing/acceptance_test.py"],
                    }
                ],
            )

            self.assertEqual(
                [item["path"] for item in context[:3]],
                ["src/_pytest/skipping.py", "testing/test_skipping.py", "testing/acceptance_test.py"],
            )
            self.assertEqual([item["priority"] for item in context[:2]], ["primary", "primary"])

    def test_failed_test_files_are_frontloaded_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))

            context = CodeEditor()._build_context(
                runtime,
                [
                    "src/_pytest/python.py",
                    "src/_pytest/skipping.py",
                    "testing/test_skipping.py",
                    "testing/test_collection.py",
                ],
                [
                    {
                        "failure_type": "logic_error",
                        "failed_tests": [
                            "testing/test_collection.py::Test_getinitialnodes::test_pkgfile",
                            "testing/test_skipping.py::test_skip_package",
                        ],
                    }
                ],
                ["src/_pytest/python.py"],
            )

            self.assertEqual(
                [item["path"] for item in context[:3]],
                ["src/_pytest/python.py", "testing/test_skipping.py", "testing/test_collection.py"],
            )

    def test_repeated_failed_patch_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "python.py").write_text(
                    'patterns = python_files + ["__init__.py"]\n',
                    encoding="utf-8",
                )
            diff = (
                "--- a/src/_pytest/python.py\n"
                "+++ b/src/_pytest/python.py\n"
                "@@ -1,1 +1,1 @@\n"
                '-patterns = python_files + ["__init__.py"]\n'
                "+patterns = python_files\n"
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.settings.llm_rule_fallback", False
            ), patch("app.agents.code_editor.build_llm_client", return_value=_FakeLLMClient(diff)):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix package collection",
                    candidate_files=["src/_pytest/python.py"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "removed_literals": ["__init__.py"],
                            "preservation_hints": ["Preserve package behavior."],
                            "diff_excerpt": diff,
                        }
                    ],
                    expected_modified_files=["src/_pytest/python.py"],
                )

            self.assertEqual(result, "")
            trace_events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            self.assertTrue(any(item["failure_type"] == "llm_repeated_failed_change" for item in failures))

    def test_removed_literal_hint_rejects_different_patch_that_deletes_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "python.py").write_text(
                    'patterns = python_files + ["__init__.py"]\nresult = patterns\n',
                    encoding="utf-8",
                )
            candidate_diff = (
                "--- a/src/_pytest/python.py\n"
                "+++ b/src/_pytest/python.py\n"
                "@@ -1,2 +1,2 @@\n"
                '-patterns = python_files + ["__init__.py"]\n'
                "+patterns = python_files\n"
                " result = patterns\n"
            )
            unrelated_previous_diff = (
                "--- a/src/_pytest/python.py\n"
                "+++ b/src/_pytest/python.py\n"
                "@@ -2,1 +2,1 @@\n"
                "-result = patterns\n"
                "+result = list(patterns)\n"
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.settings.llm_rule_fallback", False
            ), patch("app.agents.code_editor.build_llm_client", return_value=_FakeLLMClient(candidate_diff)):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix package collection",
                    candidate_files=["src/_pytest/python.py"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "removed_literals": ["__init__.py"],
                            "preservation_hints": ["Preserve package behavior."],
                            "diff_excerpt": unrelated_previous_diff,
                        }
                    ],
                    expected_modified_files=["src/_pytest/python.py"],
                )

            self.assertEqual(result, "")
            trace_events = [
                json.loads(line)
                for line in (Path(tmp) / "trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failures = [
                event["payload"]
                for event in trace_events
                if event["type"] == "llm_patch_generation" and not event["payload"].get("success")
            ]
            literal_failures = [
                item
                for item in failures
                if item["failure_type"] == "llm_repeated_failed_change"
                and "literal_removal_check" in item.get("diagnostics", {})
            ]
            self.assertTrue(literal_failures)
            self.assertIn("__init__.py", literal_failures[0]["diagnostics"]["literal_removal_check"]["removed_literals"])

    def test_different_strategy_with_same_literals_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            original = (
                "def collect_file(path):\n"
                '    patterns = python_files + ["__init__.py"]\n'
                "    if matches(path, patterns):\n"
                "        collect(path)\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest").mkdir(parents=True, exist_ok=True)
                (base / "src" / "_pytest" / "python.py").write_text(original, encoding="utf-8")
            failed_diff = (
                "--- a/src/_pytest/python.py\n"
                "+++ b/src/_pytest/python.py\n"
                "@@ -1,4 +1,4 @@\n"
                " def collect_file(path):\n"
                '-    patterns = python_files + ["__init__.py"]\n'
                "+    patterns = python_files\n"
                "     if matches(path, patterns):\n"
                "         collect(path)\n"
            )
            new_diff = (
                "--- a/src/_pytest/python.py\n"
                "+++ b/src/_pytest/python.py\n"
                "@@ -1,4 +1,6 @@\n"
                " def collect_file(path):\n"
                '     patterns = python_files + ["__init__.py"]\n'
                "+    if path.basename == \"__init__.py\" and not has_tests_nearby(path):\n"
                "+        return\n"
                "     if matches(path, patterns):\n"
                "         collect(path)\n"
            )

            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.settings.llm_rule_fallback", False
            ), patch("app.agents.code_editor.build_llm_client", return_value=_FakeLLMClient(new_diff)):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix package collection",
                    candidate_files=["src/_pytest/python.py"],
                    patch_failures=[
                        {
                            "failure_type": "logic_error",
                            "removed_literals": ["__init__.py"],
                            "preservation_hints": ["Preserve package behavior."],
                            "diff_excerpt": failed_diff,
                        }
                    ],
                    expected_modified_files=["src/_pytest/python.py"],
                )

            self.assertIn("has_tests_nearby", result)

    def test_bad_llm_diff_falls_back_to_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_demo_file(Path(tmp))
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.settings.llm_rule_fallback", True
            ), patch("app.agents.code_editor.build_llm_client", return_value=_FakeLLMClient("not a diff")):
                result = CodeEditor().generate_patch(runtime, "Fix zero price discount bug")
            self.assertIn("return 0.0", result)

    def test_prompt_contains_patch_failure_feedback(self) -> None:
        prompt = build_patch_prompt(
            issue_text="Fix a bug",
            context=[{"path": "src/app.py", "kind": "source", "content": "def f():\n    return None\n"}],
            plan=["patch src/app.py"],
            test_results=[],
            patch_failures=[
                {
                    "failure_type": "patch_apply_failed",
                    "message": "Patch context mismatch",
                    "changed_files": ["src/app.py"],
                }
            ],
        )
        self.assertIn("PREVIOUS_PATCH_APPLY_FAILURES", prompt)
        self.assertIn("Patch context mismatch", prompt)
        self.assertIn("exact current file text", prompt)
        self.assertIn("generator/iterator", prompt)
        self.assertIn("cls.__dict__.get", prompt)

    def test_mro_retry_prompt_requires_reader_and_writer_repair(self) -> None:
        prompt = build_range_edit_prompt(
            issue_text="Fix inherited pytest marks across the MRO",
            context=[
                {
                    "path": "src/_pytest/mark/structures.py",
                    "kind": "source",
                    "priority": "primary",
                    "content": (
                        "def get_unpacked_marks(obj):\n"
                        "    return normalize_mark_list(getattr(obj, 'pytestmark', []))\n"
                        "\n"
                        "def store_mark(obj, mark):\n"
                        "    obj.pytestmark = [*get_unpacked_marks(obj), mark]\n"
                    ),
                }
            ],
            plan=["collect own marks", "preserve local storage"],
            test_results=[],
            patch_failures=[
                {
                    "failure_type": "logic_error",
                    "failed_tests": ["testing/test_mark.py::test_mark_mro"],
                    "failure_excerpt": "At index 0 diff; Left contains 2 more items",
                    "preservation_hints": ["use consider_mro=False in storage"],
                }
            ],
            previous_error="previous patch failed validation",
            expected_modified_files=["src/_pytest/mark/structures.py"],
        )

        self.assertIn("TARGETED_INHERITANCE_RETRY", prompt)
        self.assertIn("actual writer/storage helper", prompt)
        self.assertIn("consider_mro=False", prompt)
        self.assertIn("Do not return another reader-only patch", prompt)

    def test_mro_storage_progress_is_not_rejected_as_repeat(self) -> None:
        previous_diff = (
            "--- a/src/_pytest/mark/structures.py\n"
            "+++ b/src/_pytest/mark/structures.py\n"
            "@@ -1,3 +1,6 @@\n"
            "-def get_unpacked_marks(obj):\n"
            "+def get_unpacked_marks(obj, *, consider_mro=True):\n"
            "+    return collect_mro_marks(obj)\n"
        )
        candidate_diff = previous_diff + (
            "+\n"
            "+def store_mark(obj, mark):\n"
            "+    obj.pytestmark = [*get_unpacked_marks(obj, consider_mro=False), mark]\n"
        )
        failure = {
            "failure_type": "logic_error",
            "failed_tests": ["testing/test_mark.py::test_mark_mro"],
            "failure_excerpt": "Left contains inherited marks in the wrong order",
        }

        self.assertIsNone(_repeated_failed_patch_change(candidate_diff, [failure]))

    def test_prompt_contains_expected_modified_files_constraint(self) -> None:
        prompt = build_patch_prompt(
            issue_text="Fix unittest skip teardown",
            context=[{"path": "src/_pytest/unittest.py", "kind": "source", "content": "def f():\n    pass\n"}],
            plan=["patch unittest integration"],
            test_results=[],
            expected_modified_files=["src/_pytest/unittest.py"],
        )

        self.assertIn("EXPECTED_MODIFIED_FILES", prompt)
        self.assertIn("src/_pytest/unittest.py", prompt)
        self.assertIn("final diff must modify only those files", prompt)

    def test_range_edit_prompt_contains_semantic_retry_rules(self) -> None:
        prompt = build_range_edit_prompt(
            issue_text="Fix MRO attribute merge",
            context=[
                {
                    "path": "src/example.py",
                    "kind": "source",
                    "content": "def f(obj):\n    return normalize(obj)\n",
                }
            ],
            plan=["patch"],
            test_results=[],
            patch_failures=[
                {
                    "failure_type": "logic_error",
                    "failure_excerpt": "assert <generator object normalize at 0x1> == [expected]",
                    "preservation_hints": ["Return a concrete list."],
                }
            ],
            previous_error="previous patch failed validation",
        )

        self.assertIn("concrete list", prompt)
        self.assertIn("cls.__dict__.get", prompt)
        self.assertIn("unexpected keyword argument", prompt)

    def test_prompt_compacts_raw_test_output_and_long_failures(self) -> None:
        raw_output = "BEGIN_RAW_OUTPUT\n" + ("traceback line\n" * 800) + "END_RAW_OUTPUT"
        prompt = build_patch_prompt(
            issue_text="Fix package collection\n" + ("long issue body\n" * 500),
            context=[{"path": "src/_pytest/python.py", "kind": "source", "content": "def f():\n    pass\n"}],
            plan=["patch"],
            test_results=[
                {
                    "status": "failed",
                    "failure_type": "logic_error",
                    "error_summary": "2 failed",
                    "raw_output": raw_output,
                    "failed_tests": ["testing/test_collection.py::test_pkg"],
                }
            ],
            patch_failures=[
                {
                    "failure_type": "logic_error",
                    "message": "failed tests" * 500,
                    "failure_excerpt": "important excerpt" * 500,
                    "diff_excerpt": "--- a/file.py\n" + ("-x\n+y\n" * 500),
                }
            ],
            compact=True,
        )

        self.assertIn("2 failed", prompt)
        self.assertIn("important excerpt", prompt)
        self.assertIn("issue text truncated", prompt)
        self.assertNotIn("BEGIN_RAW_OUTPUT", prompt)
        self.assertLess(len(prompt), 14000)

    def test_retry_context_uses_smaller_budget(self) -> None:
        context = retry_context(
            [
                {
                    "path": "src/_pytest/python.py",
                    "kind": "source",
                    "priority": "primary",
                    "content": "s" * 12000,
                },
                {
                    "path": "testing/test_collection.py",
                    "kind": "test",
                    "priority": "supporting",
                    "content": "t" * 12000,
                },
                {
                    "path": "src/_pytest/other.py",
                    "kind": "source",
                    "priority": "supporting",
                    "content": "o" * 12000,
                },
            ]
        )

        self.assertEqual(len(context[0]["content"]), 7000)
        self.assertEqual(len(context[1]["content"]), 4500)
        self.assertEqual(len(context[2]["content"]), 2000)

    def test_rule_fallback_skips_missing_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))

            with patch("app.agents.code_editor.settings.llm_provider", "offline"):
                result = CodeEditor().generate_patch(runtime, "Deduplicate should preserve order")

            self.assertEqual(result, "")

    def test_context_preserves_candidate_order_and_marks_testing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))

            context = CodeEditor()._build_context(
                runtime,
                [
                    "src/_pytest/skipping.py",
                    "testing/test_skipping.py",
                    "src/_pytest/cacheprovider.py",
                ],
                [],
            )

            self.assertEqual([item["path"] for item in context[:2]], ["src/_pytest/skipping.py", "testing/test_skipping.py"])
            self.assertEqual(context[0]["kind"], "source")
            self.assertEqual(context[1]["kind"], "test")
            self.assertEqual(context[0]["priority"], "primary")
            self.assertEqual(context[1]["priority"], "primary")

    def test_patch_retry_keeps_target_source_after_preflight_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_empty_repo(Path(tmp))
            content = "".join(f"# filler {idx}\n" for idx in range(320)) + "def target_fix():\n    return 1\n"
            for base in [runtime.workspace, runtime.baseline]:
                path = base / "src" / "app.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            bad_diff = (
                "--- a/src/app.py\n"
                "+++ b/src/app.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def missing_target():\n"
                "-    return 1\n"
                "+    return 2\n"
            )
            good_diff = (
                "--- a/src/app.py\n"
                "+++ b/src/app.py\n"
                "@@ -321,2 +321,2 @@\n"
                " def target_fix():\n"
                "-    return 1\n"
                "+    return 2\n"
            )

            class RetryClient:
                provider = "deepseek"
                model = "fake-model"

                def __init__(self) -> None:
                    self.calls = 0
                    self.prompts: list[str] = []

                def complete_json(self, messages: list[dict[str, str]], schema_name: str) -> dict:
                    self.calls += 1
                    self.prompts.append(messages[-1]["content"])
                    return {"diff": bad_diff if self.calls == 1 else good_diff, "notes": "test"}

            client = RetryClient()
            with patch("app.agents.code_editor.settings.llm_provider", "deepseek"), patch(
                "app.agents.code_editor.build_llm_client", return_value=client
            ):
                result = CodeEditor().generate_patch(
                    runtime,
                    "Fix target return behavior",
                    candidate_files=["src/app.py"],
                    plan=["patch"],
                )

            self.assertEqual(result, good_diff)
            self.assertEqual(client.calls, 2)
            self.assertIn("def target_fix():", client.prompts[1])

    def test_expected_modified_files_are_primary_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))

            context = CodeEditor()._build_context(
                runtime,
                [
                    "src/_pytest/skipping.py",
                    "testing/test_skipping.py",
                    "src/_pytest/unittest.py",
                    "testing/test_unittest.py",
                ],
                [],
                ["src/_pytest/unittest.py"],
            )

            self.assertEqual(context[0]["path"], "src/_pytest/unittest.py")
            self.assertEqual(context[0]["priority"], "primary")

    def test_long_source_context_includes_focused_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = _runtime_with_pytest_like_files(Path(tmp))
            long_source = "".join(f"# filler {idx}\n" for idx in range(900))
            long_source += (
                "class Package(Module):\n"
                "    def collect(self):\n"
                "        init_module = this_path.join(\"__init__.py\")\n"
                "        if path_matches_patterns(init_module, self.config.getini(\"python_files\")):\n"
                "            yield Module(init_module, self)\n"
            )
            for base in [runtime.workspace, runtime.baseline]:
                (base / "src" / "_pytest" / "python.py").write_text(long_source, encoding="utf-8")

            context = CodeEditor()._build_context(
                runtime,
                ["src/_pytest/python.py", "testing/test_collection.py"],
                [],
                ["src/_pytest/python.py"],
                issue_text="Do not collect random `__init__.py`; preserve Package and python_files behavior.",
            )
            retry = retry_context(context)

            self.assertNotIn("class Package", context[0]["content"])
            self.assertIn("FOCUSED", build_patch_prompt("issue", retry, [], [], compact=True))
            self.assertIn("class Package", retry[0]["focus_snippets"])
            self.assertIn("python_files", retry[0]["focus_snippets"])


def _runtime_with_demo_file(root: Path) -> ToolRuntime:
    repo = root / "repo"
    baseline = root / "baseline"
    src = repo / "src" / "patchforge_demo"
    base_src = baseline / "src" / "patchforge_demo"
    src.mkdir(parents=True)
    base_src.mkdir(parents=True)
    content = "def f():\n    return None\n\n    if price == 0:\n        return None\n"
    (src / "calculator.py").write_text(content, encoding="utf-8")
    (base_src / "calculator.py").write_text(content, encoding="utf-8")
    trace = TraceRecorder(root / "trace.jsonl")
    return ToolRuntime(repo, baseline, trace, SandboxRunner(repo))


def _runtime_with_empty_repo(root: Path) -> ToolRuntime:
    repo = root / "repo"
    baseline = root / "baseline"
    repo.mkdir(parents=True)
    baseline.mkdir(parents=True)
    trace = TraceRecorder(root / "trace.jsonl")
    return ToolRuntime(repo, baseline, trace, SandboxRunner(repo))


def _runtime_with_pytest_like_files(root: Path) -> ToolRuntime:
    repo = root / "repo"
    baseline = root / "baseline"
    for base in [repo, baseline]:
        (base / "src" / "_pytest").mkdir(parents=True)
        (base / "testing").mkdir(parents=True)
        (base / "src" / "_pytest" / "skipping.py").write_text("def skipping():\n    pass\n", encoding="utf-8")
        (base / "src" / "_pytest" / "python.py").write_text("def collect():\n    pass\n", encoding="utf-8")
        (base / "testing" / "test_skipping.py").write_text("def test_skipping():\n    pass\n", encoding="utf-8")
        (base / "testing" / "test_collection.py").write_text("def test_collection():\n    pass\n", encoding="utf-8")
        (base / "src" / "_pytest" / "unittest.py").write_text("def unittest_support():\n    pass\n", encoding="utf-8")
        (base / "testing" / "test_unittest.py").write_text("def test_unittest_support():\n    pass\n", encoding="utf-8")
        (base / "src" / "_pytest" / "cacheprovider.py").write_text("def cache():\n    pass\n", encoding="utf-8")
    trace = TraceRecorder(root / "trace.jsonl")
    return ToolRuntime(repo, baseline, trace, SandboxRunner(repo))


if __name__ == "__main__":
    unittest.main()
