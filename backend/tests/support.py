"""A scripted chat model and an API client wired to temporary storage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.base import Reply, ToolCall
from app.settings import Settings

API_KEY = "sk-test-secret-key-0000"


def call(name: str, **arguments) -> Reply:
    return Reply(
        tool_call=ToolCall(name, arguments), state={"call_id": f"call-{name}"}, total_tokens=10
    )


def answer(text: str) -> Reply:
    return Reply(text=text, total_tokens=10)


def last_result(turn) -> dict:
    """The observation of the most recent step of a turn."""
    return json.loads(turn.steps[-1].output)


class ScriptedModel:
    """Plays replies in order. Each step is a ``Reply`` or ``turn -> Reply``."""

    provider = "scripted"
    model = "scripted-model"

    def __init__(self) -> None:
        self.steps: list = []
        self.turns: list = []
        self.json_requests: list[tuple[str, str, dict]] = []
        self.json_reply = lambda system, user: {"operations": [], "rationale": "", "evidence": []}

    def decide(self, turn):
        self.turns.append(turn)
        if not self.steps:
            raise AssertionError("The scripted model ran out of replies.")
        step = self.steps.pop(0)
        return step(turn) if callable(step) else step

    def complete_json(self, system, user, schema):
        self.json_requests.append((system, user, schema))
        return self.json_reply(system, user)

    # -- helpers for assertions -------------------------------------------

    def tool_names(self, turn: int = -1) -> list[str]:
        return [tool.name for tool in self.turns[turn].tools]

    def context(self, turn: int = -1) -> dict:
        message = self.turns[turn].message
        return json.loads(message[message.index("\n{") + 1 :])


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
        self.factory_calls: list[tuple[str, str, str]] = []
        self.search = SearchStub()
        settings = Settings(
            data_dir=self.root / "data",
            knowledge_consolidation=self.knowledge_consolidation,
            cors_origins=["http://testserver"],
        )
        self.app = create_app(
            settings,
            model_factory=self.factory,
            search=self.search,
            model_lister=lambda provider, key: [f"{provider.id}-model-a", f"{provider.id}-model-b"],
        )
        self.client = TestClient(self.app)
        self.session = self.client.post("/api/sessions").json()

    def factory(self, provider, api_key, model):
        self.factory_calls.append((provider.id, api_key, model))
        return self.model

    def send(self, message: str, *steps, status: int = 200, **choice) -> dict:
        self.model.steps.extend(steps)
        response = self.client.post(
            f"/api/sessions/{self.session['id']}/messages",
            json={"api_key": API_KEY, "message": message, **choice},
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
