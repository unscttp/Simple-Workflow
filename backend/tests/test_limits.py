"""Realistic runs: step limits, the final round and permissions reaching graph stages."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.model import FINAL_ROUND_NOTE
from app.providers import PROVIDERS, create_model

from .support import ApiCase, answer, call
from .test_skills import GOOD_SURVEY


def first_invoke(turn) -> str:
    return json.loads(turn.steps[0].output)["invoke_id"]


class RealisticRunTests(ApiCase):
    def test_a_survey_without_the_graph_fits_the_step_limit(self):
        turn = self.send(
            "写一篇关于年鉴学派的综述",
            call("skill_survey-creator_invoke"),
            lambda t: call(
                "skill_survey-creator_read", invoke_id=first_invoke(t), path="templates/outline.md"
            ),
            call("workspace_request_access", folder=str(self.reports), purpose="保存综述"),
        )
        queries = [
            "Annales school",
            "Braudel longue duree",
            "Bloch Febvre",
            "mentalites",
            "Annales fourth generation",
            "Annales critical turn",
            "Annales reception China",
        ]
        steps = [call("scholar_search", query=q) for q in queries]
        steps += [
            call("web_search", query="年鉴学派"),
            lambda t: call(
                "skill_survey-creator_read",
                invoke_id=first_invoke(t),
                path="references/structure.md",
            ),
            lambda t: call(
                "skill_survey-creator_read",
                invoke_id=first_invoke(t),
                path="references/citations.md",
            ),
            call(
                "reports_save",
                title="年鉴学派综述",
                content=GOOD_SURVEY[:900],
                format="md",
                filename="annales",
            ),
            call("reports_edit", filename="annales.md", mode="append", content=GOOD_SURVEY[900:]),
            lambda t: call("skill_survey-creator_finish", invoke_id=first_invoke(t)),
            answer("已保存 annales.md"),
        ]
        turn = self.decide(turn["pending_approvals"][0]["id"], True, *steps)
        self.assertEqual(
            (turn["status"], turn["answer"]), ("completed", "已保存 annales.md"), turn["error"]
        )
        saved = (self.reports / "annales.md").read_text("utf-8")
        self.assertTrue(saved.startswith("# 年鉴学派综述"))
        self.assertTrue(saved.rstrip().endswith(GOOD_SURVEY.rstrip()[-40:]))
        self.assertEqual(self.model.context(0)["step_limit"], 30)

    def test_running_out_of_steps_ends_with_an_answer(self):
        seen = {}

        def final(turn):
            seen.update(tools=turn.tools, steps=turn.steps, message=turn.message)
            return answer("目前找到的资料有限，结论如下……")

        turn = self.send("搜索", *[call("web_search", query=f"q{i}") for i in range(30)], final)
        self.assertEqual(
            (turn["status"], turn["answer"]), ("completed", "目前找到的资料有限，结论如下……")
        )
        self.assertEqual((seen["tools"], seen["steps"]), ([], []))
        self.assertIn(FINAL_ROUND_NOTE, seen["message"])
        self.assertIn('"tool": "web_search"', seen["message"])

    def test_graph_stages_have_room_and_see_the_approved_folder(self):
        self.set_risk("high")
        turn = self.send(
            "综述",
            call("skill_survey-creator_invoke"),
            call("workspace_request_access", folder=str(self.reports), purpose="保存综述"),
        )
        turn = self.decide(
            turn["pending_approvals"][0]["id"],
            True,
            lambda t: call(
                "skill_survey-creator_run_graph",
                invoke_id=first_invoke(t),
                graph_id="survey-pipeline",
            ),
        )
        workspaces = []

        def read(path):
            def step(t):
                workspaces.append(json.loads(t.message[t.message.index("\n{") + 1 :])["workspace"])
                return call("skill_survey-creator_read", invoke_id=first_invoke(t), path=path)

            return step

        stages = [
            answer("RQ1"),
            call("scholar_search", query="Annales school"),
            answer("[1] ..."),
            call("skill_survey-creator_invoke"),
            read("references/structure.md"),
            read("templates/outline.md"),
            lambda t: call("skill_survey-creator_finish", invoke_id=first_invoke(t)),
            answer("taxonomy"),
            call("skill_survey-creator_invoke"),
            read("references/citations.md"),
            lambda t: call("skill_survey-creator_finish", invoke_id=first_invoke(t)),
            call("reports_save", title="t", content=GOOD_SURVEY[:900], format="md", filename="s"),
            call("reports_edit", filename="s.md", mode="append", content=GOOD_SURVEY[900:]),
            answer("s.md"),
            answer("done"),
        ]
        turn = self.decide(turn["pending_approvals"][0]["id"], True, *stages)
        self.assertEqual(turn["status"], "completed", turn["error"])
        graph = self.client.get(f"/api/sessions/{self.session['id']}/graph").json()["graphs"][1]
        self.assertEqual({n["status"] for n in graph["nodes"]}, {"completed"})
        self.assertEqual(set(workspaces), {str(self.reports.resolve())})
        self.assertIn(GOOD_SURVEY.rstrip()[-40:], (self.reports / "s.md").read_text("utf-8"))


class OutputLimitTests(unittest.TestCase):
    def test_deepseek_asks_for_its_longest_replies(self):
        deepseek = create_model(PROVIDERS["deepseek"], "key", "deepseek-chat")
        gemini = create_model(PROVIDERS["gemini"], "key", "g")
        self.assertEqual(deepseek._max_tokens, 8192)
        self.assertIsNone(gemini._max_tokens)
        recorder = SimpleNamespace(requests=[])
        deepseek._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **r: recorder.requests.append(r) or 1 / 0)
            ),
            api_key="key",
        )
        with self.assertRaises(ZeroDivisionError):
            deepseek._create(messages=[])
        self.assertEqual(recorder.requests[0]["max_tokens"], 8192)


if __name__ == "__main__":
    unittest.main()
