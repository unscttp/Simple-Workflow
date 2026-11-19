"""A scripted chat model and an API client wired to temporary storage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.model import ModelReply, ToolCall
from app.settings import Settings

API_KEY = "sk-test-secret-key-0000"


def call(name: str, **arguments) -> ModelReply:
    return ModelReply(tool_calls=[ToolCall(name, arguments)], total_tokens=10)


def answer(text: str) -> ModelReply:
    return ModelReply(text=text, total_tokens=10)


class ScriptedModel:
    """Plays chat replies in order. Each step is a reply or ``(messages, tools) -> reply``."""

    def __init__(self) -> None:
        self.steps: list = []
        self.json_replies: list = []
        self.requests: list[tuple[list[dict], list[dict]]] = []

    def complete(self, messages, tools):
        self.requests.append((messages, tools))
        if not self.steps:
            raise AssertionError("The scripted model ran out of replies.")
        step = self.steps.pop(0)
        return step(messages, tools) if callable(step) else step

    def complete_json(self, messages):
        self.requests.append((messages, []))
        return self.json_replies.pop(0) if self.json_replies else {"operations": []}

    # -- helpers for assertions -------------------------------------------

    def tool_names(self, request: int = -1) -> list[str]:
        return [tool["function"]["name"] for tool in self.requests[request][1]]

    def context(self, request: int = -1) -> dict:
        system = self.requests[request][0][0]["content"]
        return json.loads(system[system.index("{") :])

    def last_tool_result(self, request: int = -1) -> dict:
        tool_messages = [m for m in self.requests[request][0] if m["role"] == "tool"]
        return json.loads(tool_messages[-1]["content"])


class SearchStub:
    def __init__(self) -> None:
        self.results = [
            {"title": "Agent trends", "body": "Agents use tools.", "href": "https://example.com"}
        ]

    def __call__(self, query, max_results):
        return self.results[:max_results]


class ApiCase(unittest.TestCase):
    """Fresh data folder, scripted model and search stub for every test."""

    knowledge_consolidation = False

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reports = self.root / "reports"
        self.model = ScriptedModel()
        self.search = SearchStub()
        settings = Settings(
            data_dir=self.root / "data",
            knowledge_consolidation=self.knowledge_consolidation,
            cors_origins=["http://testserver"],
        )
        self.app = create_app(
            settings, model_factory=lambda key, name: self.model, search=self.search
        )
        self.client = TestClient(self.app)
        self.session = self.client.post("/api/sessions").json()

    def send(self, message: str, *steps, status: int = 200) -> dict:
        self.model.steps.extend(steps)
        response = self.client.post(
            f"/api/sessions/{self.session['id']}/messages",
            json={"api_key": API_KEY, "message": message},
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def decide(self, request_id: str, approved: bool, *steps, status: int = 200) -> dict:
        self.model.steps.extend(steps)
        response = self.client.post(
            f"/api/sessions/{self.session['id']}/approvals/{request_id}",
            json={"api_key": API_KEY, "approved": approved},
        )
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def set_risk(self, level: str) -> dict:
        response = self.client.put(
            f"/api/sessions/{self.session['id']}/risk-level", json={"risk_level": level}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def grant_workspace(self) -> None:
        turn = self.send(
            "请保存报告",
            call("workspace_request_access", folder=str(self.reports), purpose="保存报告"),
        )
        self.decide(turn["pending_approvals"][0]["id"], True, answer("目录已授权。"))
