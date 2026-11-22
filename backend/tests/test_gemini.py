"""Gemini compatibility: tool schemas in Gemini's subset, request shape, error reasons."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
from workflow_graph_tools import GraphTools

from app.agent import AgentService
from app.providers import ModelError
from app.providers.base import Step, ToolSpec, Turn, provider_message
from app.providers.chat_completions import ChatCompletionsModel
from app.providers.gemini_schema import KEPT, gemini_parameters
from app.settings import Settings

from .test_providers import Recorder, completion


def problems(schema, path="$") -> list[str]:
    """Everything in a converted schema that Gemini would refuse."""
    found = [f"{path}: {key}" for key in schema if key not in KEPT | {"format"}]
    if isinstance(schema.get("type"), list):
        found.append(f"{path}: type list")
    if "enum" in schema and (
        schema.get("type") != "string" or not all(isinstance(v, str) for v in schema["enum"])
    ):
        found.append(f"{path}: enum")
    for name, sub in (schema.get("properties") or {}).items():
        found += problems(sub, f"{path}.{name}")
    if "items" in schema:
        found += problems(schema["items"], f"{path}[]")
    for index, option in enumerate(schema.get("anyOf", [])):
        found += problems(option, f"{path}|{index}")
    return found


class SchemaTests(unittest.TestCase):
    def test_conversion_rules(self):
        schema = {
            "type": "object",
            "$defs": {"risk": {"enum": ["low", "high"]}},
            "properties": {
                "parent": {"type": ["string", "null"]},
                "mode": {"enum": ["a", "b"]},
                "risk": {"$ref": "#/$defs/risk"},
                "fixed": {"const": "x"},
                "maybe": {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 1}]},
                "count": {"enum": [1, 2]},
                "map": {"type": "object", "additionalProperties": {"type": "string"}},
                "tags": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
            },
            "required": ["mode", "ghost"],
            "additionalProperties": False,
        }
        out = gemini_parameters(schema)
        props = out["properties"]
        self.assertEqual(props["parent"], {"type": "string", "nullable": True})
        self.assertEqual(props["mode"], {"type": "string", "enum": ["a", "b"]})
        self.assertEqual(props["risk"], {"type": "string", "enum": ["low", "high"]})
        self.assertEqual(props["fixed"], {"type": "string", "enum": ["x"]})
        self.assertEqual(props["maybe"], {"type": "integer", "minimum": 1, "nullable": True})
        self.assertEqual(props["count"], {"type": "integer"})
        self.assertEqual(props["map"], {"type": "object"})
        self.assertEqual(props["tags"], {"type": "array", "items": {"type": "string"}})
        self.assertEqual(out["required"], ["mode"])
        self.assertNotIn("additionalProperties", out)
        self.assertEqual(problems(out), [])
        self.assertIsNone(gemini_parameters({"type": "object", "properties": {}}))

    def test_every_tool_of_the_app_converts_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AgentService(
                Settings(data_dir=Path(folder), knowledge_consolidation=False),
                lambda *args: None,
                search=lambda query, limit: [],
            )
            schemas = {tool.name: tool.input_schema for tool in service.tools.list_tools()}
        schemas["graph.create"] = GraphTools(None).get_tool("graph.create").input_schema
        for name, schema in schemas.items():
            with self.subTest(tool=name):
                converted = gemini_parameters(schema)
                if converted is not None:
                    self.assertEqual(problems(converted), [])
        self.assertLess(len(str(gemini_parameters(schemas["graph.create"]))), 20000)


TOOLS = [
    ToolSpec(
        "reports_list", "List.", {"type": "object", "properties": {}, "additionalProperties": False}
    ),
    ToolSpec(
        "skill_invoke",
        "Open.",
        {
            "type": "object",
            "properties": {"parent_invoke_id": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
    ),
]


def turn_with_step() -> Turn:
    step = Step("skill_invoke", {}, "call-1", '{"ok": true}', state={"call_id": "call-1"})
    return Turn("S", [], "hi", TOOLS, [step])


class RequestTests(unittest.TestCase):
    def model(self, provider):
        recorder = Recorder(completion({"role": "assistant", "content": "done"}))
        client = SimpleNamespace(chat=SimpleNamespace(completions=recorder), api_key="AIzaSECRET")
        return ChatCompletionsModel(provider, "m", client), recorder

    def test_gemini_requests(self):
        model, recorder = self.model("gemini")
        model.decide(turn_with_step())
        request = recorder.requests[0]
        listing, invoke = (tool["function"] for tool in request["tools"])
        self.assertNotIn("parameters", listing)  # no parameters instead of an empty object
        self.assertEqual(
            invoke["parameters"]["properties"]["parent_invoke_id"],
            {"type": "string", "nullable": True},
        )
        assistant, result = request["messages"][-2:]
        self.assertIsNone(assistant["content"])  # Gemini refuses empty text parts
        self.assertEqual(result["name"], "skill_invoke")

    def test_other_providers_get_the_schemas_unchanged(self):
        model, recorder = self.model("deepseek")
        model.decide(turn_with_step())
        request = recorder.requests[0]
        self.assertEqual(request["tools"][0]["function"]["parameters"], TOOLS[0].parameters)
        self.assertNotIn("name", request["messages"][-1])


class ErrorTests(unittest.TestCase):
    def test_provider_reason_is_shown_without_keys(self):
        body = {
            "error": {
                "code": 400,
                "message": (
                    "Invalid value at 'tools[0]' for key AIzaSyDUMMYDUMMYDUMMYDUMMY12"
                    " and sk-abcdefgh123"
                ),
                "status": "INVALID_ARGUMENT",
            }
        }
        self.assertEqual(
            provider_message(body),
            "Invalid value at 'tools[0]' for key [key] and [key]",
        )
        self.assertEqual(
            provider_message([body], ("tools",)),
            "Invalid value at '[key][0]' for key [key] and [key]",
        )

        request = httpx.Request("POST", "https://example.com")
        error = openai.APIStatusError(
            "x", response=httpx.Response(400, request=request), body=body["error"]
        )
        recorder = Recorder(error)
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=recorder), api_key="sk-abcdefgh123"
        )
        model = ChatCompletionsModel("gemini", "m", client)
        with self.assertRaises(ModelError) as caught:
            model.decide(Turn("S", [], "hi", []))
        message = str(caught.exception)
        self.assertTrue(message.startswith("Model request failed with HTTP 400: Invalid value"))
        self.assertNotIn("sk-abcdefgh123", message)
        self.assertNotIn("AIzaSy", message)


if __name__ == "__main__":
    unittest.main()
