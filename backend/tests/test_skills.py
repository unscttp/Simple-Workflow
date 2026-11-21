"""The survey-creator skill, the survey pipeline as a generated graph, and the graph view."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest

from app.model import STAGE_NOTE
from app.tools import SKILLS_DIR

from .support import ApiCase, answer, call, last_result

SKILL = SKILLS_DIR / "survey-creator"
PIPELINE = SKILL / "graphs" / "survey-pipeline.json"

GOOD_SURVEY = """# A Survey of Retrieval-Augmented Generation

## Abstract
RAG combines retrieval with generation [1].

## 1. Introduction
Retrieval helps [1, 2].

## 2. Background
Terms.

## 3. Method
Searched the web.

## 4. Taxonomy of Approaches
Two families [2-3].

## 5. Comparison
| Work | Technique |
| --- | --- |
| Lewis et al. [1] | retrieve then generate |

## 6. Open Challenges
Evaluation [3].

## 7. Future Directions
Better retrieval.

## 8. Conclusion
Done.

## References
[1] Lewis, P. (2020). Retrieval-augmented generation. https://arxiv.org/abs/2005.11401
[2] Gao, Y. (2024). RAG survey. https://arxiv.org/abs/2312.10997
[3] Example. (2025). Benchmark [unverified: venue]. https://example.org/b
"""


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_survey", SKILL / "scripts" / "check_survey.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke_id(turn, back: int = 1) -> str:
    return json.loads(turn.steps[-back].output)["invoke_id"]


class SkillPackageTests(unittest.TestCase):
    def test_pipeline_graph_matches_its_scenario_and_is_valid(self):
        from workflow_core import DirectedGraph, WorkflowDefinition

        document = json.loads(PIPELINE.read_text("utf-8"))
        scenario = (PIPELINE.parent / document["usage_scenario_file"]).read_bytes()
        self.assertEqual(hashlib.sha256(scenario).hexdigest(), document["usage_scenario_sha256"])
        graph = DirectedGraph.from_dict(document["graph"])
        WorkflowDefinition("p", "P", graph.nodes, graph.edges, document["start"]).assert_valid()
        self.assertEqual([n.id for n in graph.nodes], ["scope", "collect", "organise", "write"])
        self.assertTrue(all(n.config.get("instruction") for n in graph.nodes))

    def test_checker_accepts_a_good_survey_and_reports_problems(self):
        checker = load_checker()
        report = checker.check(GOOD_SURVEY, 2026)
        self.assertTrue(report["passed"], report["problems"])
        self.assertEqual(report["references"]["count"], 3)
        self.assertEqual(report["references"]["unverified"], 1)
        self.assertEqual(report["references"]["recent_share"], 0.67)

        broken = GOOD_SURVEY.replace("Two families [2-3].", "Two families [3] [5].")
        broken = broken.replace("Retrieval helps [1, 2].", "Retrieval helps [1].").replace(
            "| Work | Technique |\n| --- | --- |\n", ""
        )
        report = checker.check(broken.replace("## 3. Method\n", "## 3. Sources\n"), 2026)
        problems = " ".join(report["problems"])
        self.assertFalse(report["passed"])
        for expected in (
            "method",
            "Cited but not listed: [5]",
            "Listed but never cited: [2]",
            "table",
        ):
            self.assertIn(expected, problems)


class SkillToolTests(ApiCase):
    def test_skill_tools_follow_the_risk_level(self):
        self.send("你好", answer("你好"))
        names = self.model.tool_names()
        for op in ("invoke", "read", "finish"):
            self.assertIn(f"skill_survey-creator_{op}", names)
        self.assertNotIn("skill_survey-creator_script", names)  # high risk
        self.assertNotIn("graph_create", names)  # high risk
        self.set_risk("high")
        self.send("你好", answer("你好"))
        self.assertIn("skill_survey-creator_script", self.model.tool_names())
        self.assertIn("graph_create", self.model.tool_names())

    def test_invoke_read_and_finish(self):
        turn = self.send(
            "写一篇综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_read", invoke_id=invoke_id(t), path="templates/outline.md"
            ),
            lambda t: call("skill_survey-creator_finish", invoke_id=invoke_id(t, 2)),
            answer("好的"),
        )
        self.assertEqual(turn["status"], "completed")
        opened = json.loads(self.model.turns[1].steps[0].output)
        self.assertIn("# Survey creator", opened["instructions"])
        self.assertEqual(opened["metadata"]["name"], "survey-creator")
        outline = json.loads(self.model.turns[2].steps[1].output)["content"]
        self.assertEqual(outline, (SKILL / "templates" / "outline.md").read_text("utf-8"))
        self.assertEqual(
            [log["tool_name"] for log in turn["tool_logs"]],
            [f"skill.survey-creator.{op}" for op in ("invoke", "read", "finish")],
        )
        summaries = [entry["summary"] for entry in turn["audit_entries"]]
        self.assertIn("Skill opened: survey-creator", summaries)
        self.assertIn("Skill closed: survey-creator", summaries)

    def test_files_outside_the_package_are_refused(self):
        turn = self.send(
            "读取",
            call("skill_survey-creator_invoke"),
            lambda t: call("skill_survey-creator_read", invoke_id=invoke_id(t), path="../SKILL.md"),
            lambda t: answer(last_result(t)["error"]["type"]),
        )
        self.assertIn("ValueError", turn["answer"])

    def test_check_script_runs_after_approval(self):
        self.set_risk("high")
        self.grant_workspace()
        path = self.reports / "rag.md"
        path.write_text(GOOD_SURVEY, encoding="utf-8")
        turn = self.send(
            "检查综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_script",
                invoke_id=invoke_id(t),
                path="scripts/check_survey.py",
                argv=[str(path)],
            ),
        )
        (approval,) = turn["pending_approvals"]
        self.assertEqual(approval["tool_name"], "skill.survey-creator.script")
        self.assertEqual(approval["arguments"]["argv"], [str(path)])
        turn = self.decide(
            approval["id"],
            True,
            lambda t: answer(str(json.loads(last_result(t)["stdout"])["passed"])),
        )
        self.assertEqual(turn["answer"], "True")
        self.assertIn(
            "Skill script ran: scripts/check_survey.py",
            [e["summary"] for e in turn["audit_entries"]],
        )


SURVEY_REPORT = GOOD_SURVEY


class SurveyPipelineTests(ApiCase):
    """The skill's graph runs as a reviewed child task; the graph view follows it."""

    def pipeline_call(self, turn):
        document = json.loads(last_result(turn)["content"])
        return call(
            "graph_create",
            nodes=document["graph"]["nodes"],
            edges=document["graph"]["edges"],
            start=document["start"],
            explanation=document["explanation"],
        )

    def test_pipeline_runs_after_review_and_the_view_follows_it(self):
        self.set_risk("high")
        self.grant_workspace()
        turn = self.send(
            "写一篇关于检索增强生成的综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_read",
                invoke_id=invoke_id(t),
                path="graphs/survey-pipeline.json",
            ),
            self.pipeline_call,
        )
        (approval,) = turn["pending_approvals"]
        self.assertEqual(approval["kind"], "graph_creation")
        self.assertEqual(approval["skill"], "survey-creator")
        self.assertEqual(
            [node["id"] for node in approval["graph"]["nodes"]],
            ["scope", "collect", "organise", "write"],
        )

        view = self.graph()
        root, child = view["graphs"]
        self.assertEqual(root["nodes"][0]["status"], "waiting")
        self.assertEqual(view["current"][0]["kind"], "approval")
        self.assertEqual(
            (child["status"], child["parent"]["node"]), ("awaiting_review", "assistant")
        )
        self.assertEqual({n["status"] for n in child["nodes"]}, {"pending"})

        live_views = []

        def look_then_answer(t):
            live_views.append(self.app.state.agent.graph(self.session["id"]))
            return answer("[1] Agent trends. https://example.com")

        turn = self.decide(
            approval["id"],
            True,
            answer("RQ1: 方法有哪些？ 查询: retrieval augmented generation survey"),
            call("web_search", query="retrieval augmented generation survey"),
            look_then_answer,
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_read", invoke_id=invoke_id(t), path="references/structure.md"
            ),
            lambda t: call("skill_survey-creator_finish", invoke_id=invoke_id(t, 2)),
            answer("分类：两类。"),
            call(
                "reports_save",
                title="检索增强生成综述",
                content=SURVEY_REPORT,
                format="md",
                filename="rag-survey",
            ),
            answer("rag-survey.md：摘要……"),
            lambda t: answer(last_result(t)["result"]["survey"]["report"]),
        )
        self.assertEqual(turn["status"], "completed", turn)
        self.assertEqual(turn["answer"], "rag-survey.md：摘要……")
        self.assertTrue((self.reports / "rag-survey.md").exists())

        stage_turns = [t for t in self.model.turns if t.message.startswith(STAGE_NOTE)]
        self.assertEqual(len(stage_turns), 9)
        collect = stage_turns[1]
        self.assertIn("RQ1", collect.message)  # the scope node's output reached the next node
        self.assertNotIn("graph_create", [tool.name for tool in collect.tools])
        self.assertNotIn("reports_save", [tool.name for tool in collect.tools])
        self.assertIn("reports_save", [tool.name for tool in stage_turns[-2].tools])

        (during,) = live_views
        root, child = during["graphs"]
        self.assertTrue(during["running"])
        self.assertEqual(root["nodes"][0]["activity"]["kind"], "graph")
        self.assertEqual(child["status"], "running")
        self.assertEqual(
            [(n["id"], n["status"]) for n in child["nodes"]],
            [
                ("scope", "completed"),
                ("collect", "running"),
                ("organise", "pending"),
                ("write", "pending"),
            ],
        )
        self.assertEqual(
            [(c["graph"], c["node"], c["kind"]) for c in during["current"]],
            [(root["id"], "assistant", "graph"), (child["id"], "collect", "model")],
        )

        after = self.graph()
        self.assertFalse(after["running"])
        root, child = after["graphs"]
        self.assertEqual((root["status"], child["status"]), ("completed", "completed"))
        self.assertEqual({n["status"] for n in child["nodes"]}, {"completed"})
        self.assertEqual(child["nodes"][2]["tool_calls"], 3)
        self.assertEqual(after["current"], [])
        tools = [log["tool_name"] for log in turn["tool_logs"]]
        self.assertIn("web.search", tools)
        self.assertIn("reports.save", tools)
        self.assertEqual(self.app.state.agent.live.children(after["task_id"]), [])

    def test_rejecting_the_graph_lets_the_model_continue(self):
        self.set_risk("high")
        turn = self.send(
            "综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_read",
                invoke_id=invoke_id(t),
                path="graphs/survey-pipeline.json",
            ),
            self.pipeline_call,
        )
        turn = self.decide(turn["pending_approvals"][0]["id"], False)
        self.assertEqual(turn["status"], "failed")
        self.assertEqual(self.graph()["graphs"][1]["status"], "awaiting_review")

    def graph(self) -> dict:
        response = self.client.get(f"/api/sessions/{self.session['id']}/graph")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class GraphViewTests(ApiCase):
    def test_idle_session_shows_the_workflow(self):
        view = self.client.get(f"/api/sessions/{self.session['id']}/graph").json()
        self.assertEqual((view["task_id"], view["status"], view["running"]), (None, "idle", False))
        (root,) = view["graphs"]
        self.assertEqual(root["id"], "chat-turn")
        self.assertEqual(root["nodes"][0]["status"], "pending")
        self.assertEqual(root["nodes"][0]["allowance"], "risk-medium")

    def test_view_while_the_model_thinks_and_after(self):
        seen = []

        def look(turn):
            seen.append(self.app.state.agent.graph(self.session["id"]))
            return answer("好")

        turn = self.send("你好", look)
        (during,) = seen
        self.assertTrue(during["running"])
        self.assertEqual(during["current"][0]["kind"], "model")
        after = self.client.get(f"/api/sessions/{self.session['id']}/graph").json()
        self.assertEqual((after["task_id"], after["status"]), (turn["task_id"], "completed"))
        node = after["graphs"][0]["nodes"][0]
        self.assertEqual((node["status"], node["decisions"]), ("completed", 1))

    def test_foreign_and_unknown_tasks(self):
        other = self.client.post("/api/sessions").json()
        turn = self.send("你好", answer("好"))
        foreign = self.client.get(
            f"/api/sessions/{other['id']}/graph", params={"task_id": turn["task_id"]}
        ).json()
        self.assertIsNone(foreign["task_id"])
        bad = self.client.get(
            f"/api/sessions/{self.session['id']}/graph", params={"task_id": "../../etc"}
        )
        self.assertEqual(bad.json()["task_id"], None)
        self.assertEqual(self.client.get("/api/sessions/" + "0" * 32 + "/graph").status_code, 404)


if __name__ == "__main__":
    unittest.main()
