"""End-to-end flows through the HTTP API with a scripted model."""

from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.providers import ModelError

from .support import API_KEY, ApiCase, answer, call, last_result


class ChatTests(ApiCase):
    def test_answer_without_tools(self):
        turn = self.send("你好", answer("你好！"))
        self.assertEqual((turn["status"], turn["answer"]), ("completed", "你好！"))
        self.assertEqual(
            [(m["role"], m["content"]) for m in turn["session"]["messages"]],
            [("user", "你好"), ("assistant", "你好！")],
        )
        seen = self.model.turns[0]
        self.assertTrue(seen.message.startswith("你好\n\n"))
        self.assertEqual(seen.conversation, [])
        self.assertIn("reports_save", self.model.tool_names())
        self.assertNotIn("reports_delete", self.model.tool_names())  # default risk is medium

    def test_conversation_and_tool_results_reach_the_model(self):
        self.send("第一句", answer("收到"))
        turn = self.send(
            "搜索 agent 趋势",
            call("web_search", query="agent trends"),
            lambda turn: answer(last_result(turn)["results"][0]["title"]),
        )
        self.assertEqual(turn["answer"], "Agent trends")
        seen = self.model.turns[-1]
        self.assertEqual([m["role"] for m in seen.conversation], ["user", "assistant"])
        self.assertEqual(seen.steps[-1].tool, "web_search")
        (log,) = turn["tool_logs"]
        self.assertEqual((log["tool_name"], log["status"]), ("web.search", "completed"))

    def test_invalid_arguments_and_errors_are_shown_to_the_model(self):
        turn = self.send(
            "分析",
            call("data_analyze_trend", data_json="not json"),
            call("data_analyze_trend", data_json="[1, 2, 4]"),
            lambda turn: answer(str(last_result(turn)["mean"])),
        )
        self.assertEqual(turn["answer"], "2.3333")
        self.assertEqual([log["status"] for log in turn["tool_logs"]], ["failed", "completed"])
        self.assertIn("ValueError", turn["tool_logs"][0]["output"])

    def test_model_error_fails_the_turn_without_leaking_the_key(self):
        def broken(turn):
            raise ModelError("Model request failed with HTTP 401.")

        turn = self.send("你好", broken)
        self.assertEqual(turn["status"], "failed")
        self.assertIn("HTTP 401", turn["error"])
        stored = "".join(p.read_text("utf-8") for p in self.root.rglob("*") if p.is_file())
        self.assertNotIn(API_KEY, stored)

    def test_unknown_session(self):
        self.assertEqual(self.client.get("/api/sessions/" + "0" * 32).status_code, 404)
        self.assertEqual(self.client.get("/api/sessions/../etc").status_code, 404)


class ApprovalTests(ApiCase):
    def test_folder_access_needs_the_user_and_then_reports_can_be_saved(self):
        turn = self.send(
            "把结论保存成报告",
            call("workspace_request_access", folder=str(self.reports), purpose="保存报告"),
        )
        self.assertEqual(turn["status"], "waiting")
        (approval,) = turn["pending_approvals"]
        self.assertEqual(approval["tool_name"], "workspace.request_access")
        self.assertEqual(approval["arguments"]["folder"], str(self.reports))
        self.assertIsNone(turn["session"]["workspace"])
        self.assertEqual(
            self.client.get(f"/api/sessions/{self.session['id']}").json()["pending_approvals"][0][
                "id"
            ],
            approval["id"],
        )
        self.send("另一个问题", status=409)

        turn = self.decide(
            approval["id"],
            True,
            call("reports_save", title="结论", content="Agent 正在普及。", format="md"),
            answer("已保存。"),
        )
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["session"]["workspace"], str(self.reports.resolve()))
        statuses = [(log["tool_name"], log["status"]) for log in turn["tool_logs"]]
        self.assertEqual(
            statuses,
            [
                ("workspace.request_access", "approved"),
                ("workspace.request_access", "completed"),
                ("reports.save", "completed"),
            ],
        )
        (saved,) = list(self.reports.glob("*.md"))
        self.assertIn("Agent 正在普及。", saved.read_text("utf-8"))
        audit = [entry["type"] for entry in turn["audit_entries"]]
        for expected in (
            "approval.requested",
            "approval.resolved",
            "workspace.granted",
            "report.saved",
        ):
            self.assertIn(expected, audit)

    def test_model_cannot_use_files_without_an_approved_folder(self):
        turn = self.send(
            "保存",
            call("reports_save", title="t", content="c", format="md"),
            lambda turn: answer(last_result(turn)["error"]["type"]),
        )
        self.assertEqual(turn["answer"], "PermissionError")
        self.assertFalse(self.reports.exists())

    def test_rejection_ends_the_turn_and_unblocks_the_session(self):
        self.grant_workspace()
        (self.reports / "old.md").write_text("# Old\n", "utf-8")
        self.set_risk("high")
        turn = self.send("删除 old.md", call("reports_delete", filename="old.md"))
        self.assertEqual(turn["pending_approvals"][0]["risk_level"], "high")
        turn = self.decide(turn["pending_approvals"][0]["id"], False)
        self.assertEqual(turn["status"], "failed")
        self.assertEqual(turn["tool_logs"][-1]["status"], "rejected")
        self.assertIn("拒绝", turn["answer"])
        self.assertTrue((self.reports / "old.md").exists())
        self.assertEqual(self.send("继续", answer("好的"))["status"], "completed")

    def test_approved_delete_and_unknown_requests(self):
        self.grant_workspace()
        (self.reports / "old.md").write_text("# Old\n", "utf-8")
        self.set_risk("high")
        turn = self.send("删除 old.md", call("reports_delete", filename="old.md"))
        self.decide("approval_000000000000", True, status=404)
        turn = self.decide(turn["pending_approvals"][0]["id"], True, answer("已删除。"))
        self.assertEqual(turn["status"], "completed")
        self.assertFalse((self.reports / "old.md").exists())
        self.decide("approval_000000000000", True, status=409)

    def test_system_folders_are_refused_even_when_approved(self):
        turn = self.send(
            "保存", call("workspace_request_access", folder="/etc/reports", purpose="x")
        )
        turn = self.decide(
            turn["pending_approvals"][0]["id"],
            True,
            lambda turn: answer(last_result(turn)["error"]["message"]),
        )
        self.assertIn("System folders", turn["answer"])
        self.assertIsNone(turn["session"]["workspace"])

    def test_revoking_the_folder_stops_file_tools(self):
        self.grant_workspace()
        revoked = self.client.delete(f"/api/sessions/{self.session['id']}/workspace").json()
        self.assertIsNone(revoked["workspace"])
        turn = self.send(
            "列出报告",
            call("reports_list"),
            lambda turn: answer(last_result(turn)["error"]["type"]),
        )
        self.assertEqual(turn["answer"], "PermissionError")


class RiskLevelTests(ApiCase):
    def test_risk_level_decides_which_tools_the_model_gets(self):
        self.set_risk("low")
        self.send("hi", answer("ok"))
        low = set(self.model.tool_names())
        self.assertIn("reports_read", low)
        self.assertFalse(
            {"reports_save", "reports_edit", "reports_delete", "knowledge_propose"} & low
        )
        self.set_risk("high")
        self.send("hi", answer("ok"))
        self.assertIn("reports_delete", self.model.tool_names())

    def test_hidden_tools_are_still_refused_if_called(self):
        self.grant_workspace()
        self.set_risk("low")
        turn = self.send(
            "保存",
            call("reports_save", title="t", content="c", format="md"),
            lambda turn: answer(last_result(turn)["error"]["message"]),
        )
        self.assertIn("exceeds allowance", turn["answer"])
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_lowering_the_risk_level_while_waiting_applies_on_resume(self):
        self.grant_workspace()
        (self.reports / "old.md").write_text("# Old\n", "utf-8")
        self.set_risk("high")
        turn = self.send("删除", call("reports_delete", filename="old.md"))
        self.set_risk("medium")
        turn = self.decide(
            turn["pending_approvals"][0]["id"],
            True,
            lambda turn: answer(last_result(turn)["error"]["type"]),
        )
        self.assertEqual(turn["answer"], "PermissionError")
        self.assertTrue((self.reports / "old.md").exists())

    def test_invalid_risk_level(self):
        response = self.client.put(
            f"/api/sessions/{self.session['id']}/risk-level", json={"risk_level": "root"}
        )
        self.assertEqual(response.status_code, 422)


class KnowledgeTests(ApiCase):
    knowledge_consolidation = True

    def test_turn_lessons_wait_for_review_then_reach_later_turns(self):
        def propose(system, user):
            request = json.loads(user)
            evidence = [
                e["id"] for e in request["task_memory"]["events"] if e["type"] == "tool.completed"
            ]
            return {
                "operations": [
                    {
                        "op": "create_page",
                        "page": "assistant/agents",
                        "title": "Agents",
                        "summary": "Agent 趋势",
                    },
                    {"op": "add_entry", "page": "assistant/agents", "text": "Agent 普遍使用工具。"},
                ],
                "rationale": "搜索确认。",
                "evidence": evidence,
            }

        self.model.json_reply = propose
        self.send(
            "搜索 agent 趋势", call("web_search", query="agent"), answer("Agent 普遍使用工具。")
        )

        knowledge = self.client.get("/api/knowledge").json()
        self.assertEqual(knowledge["pages"], [])
        (proposal,) = knowledge["proposals"]
        self.assertEqual(proposal["source"]["kind"], "external")

        reviewed = self.client.post(
            f"/api/knowledge/proposals/{proposal['id']}", json={"approved": True}
        ).json()
        self.assertEqual(reviewed["status"], "applied")
        page = self.client.get("/api/knowledge/pages/assistant/agents").json()
        self.assertIn("Agent 普遍使用工具。", page["content"])

        self.model.json_reply = lambda system, user: {"operations": []}
        self.send("agent 有什么特点？", answer("见知识库。"))
        self.assertEqual(self.model.context()["knowledge"]["pages"][0]["page"], "assistant/agents")

        revert = self.client.post(f"/api/knowledge/changes/{reviewed['change_id']}/revert")
        self.assertEqual(revert.status_code, 200)
        self.assertEqual(self.client.get("/api/knowledge").json()["pages"], [])


class ConfigurationTests(ApiCase):
    def test_every_tool_is_listed_in_every_risk_allowance(self):
        agent = self.app.state.agent
        names = {f"tool:{tool.name}" for tool in agent.tools.list_tools()}
        names |= {f"tool:knowledge.{op}" for op in ("list", "read", "search", "propose")}
        names.add("tool:graph.create")
        for level in ("low", "medium", "high"):
            allowance = agent.allowances.resolve(f"risk-{level}")
            self.assertEqual(set(allowance.actions), names, level)
            self.assertEqual(allowance.max_tool_risk, level)


class ProviderTests(ApiCase):
    def test_providers_and_their_models(self):
        view = self.client.get("/api/providers").json()
        self.assertEqual(view["default"], "deepseek")
        self.assertEqual(
            [p["id"] for p in view["providers"]], ["deepseek", "anthropic", "openai", "gemini"]
        )
        self.assertEqual(view["providers"][1]["default_model"], "claude-opus-5-5")
        self.assertNotIn("base_url", view["providers"][0])

        listed = self.client.post("/api/providers/gemini/models", json={"api_key": API_KEY})
        self.assertEqual(
            listed.json(), {"provider": "gemini", "models": ["gemini-model-a", "gemini-model-b"]}
        )
        missing = self.client.post("/api/providers/nope/models", json={"api_key": API_KEY})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.post("/api/providers/openai/models", json={}).status_code, 422)

    def test_listing_errors_do_not_leak_the_key(self):
        def failing(provider, key):
            raise ModelError("Listing models failed with HTTP 401.")

        client = TestClient(
            create_app(
                self.app.state.agent.settings, model_factory=self.factory, model_lister=failing
            )
        )
        response = client.post("/api/providers/openai/models", json={"api_key": API_KEY})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(API_KEY, response.text)

    def test_each_request_chooses_its_model(self):
        self.send("一", answer("a"))
        self.send("二", answer("b"), provider="anthropic")
        self.send("三", answer("c"), provider="gemini", model="gemini-custom")
        self.assertEqual(
            self.factory_calls,
            [
                ("deepseek", API_KEY, "deepseek-v4-pro"),
                ("anthropic", API_KEY, "claude-opus-5-5"),
                ("gemini", API_KEY, "gemini-custom"),
            ],
        )
        self.send("四", status=422, provider="mistral")

    def test_stats_and_model_labels(self):
        turn = self.send(
            "保存",
            call("workspace_request_access", folder=str(self.reports), purpose="保存报告"),
        )
        self.assertEqual(turn["stats"]["decisions"], 1)
        turn = self.decide(turn["pending_approvals"][0]["id"], True, answer("好了"))
        stats = turn["stats"]
        self.assertEqual(stats["models"], [{"provider": "scripted", "model": "scripted-model"}])
        self.assertEqual((stats["decisions"], stats["tokens"], stats["tool_calls"]), (2, 20, 1))
        self.assertFalse(stats["tokens_estimated"])
        self.assertEqual(turn["session"]["messages"][-1]["model"], "scripted/scripted-model")
        self.assertIsNone(turn["session"]["messages"][0]["model"])


if __name__ == "__main__":
    unittest.main()
