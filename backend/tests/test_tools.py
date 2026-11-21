"""Tool behaviour that does not need the API: documents, analysis, file rules, adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from workflow_core import MeteredDecision, NodeResult

from app.model import NO_ANSWER, AgentAdapter, _digest, native_tools
from app.providers.base import Reply, ToolCall, function_name
from app.sessions import SessionStore
from app.tools import analysis
from app.tools.documents import edit_docx, edit_markdown, read_text, write_docx, write_pdf
from app.tools.reports import ReportTools


class AnalysisTests(unittest.TestCase):
    def test_statistics(self):
        result = analysis.analyze_trend(
            None, None, {"data_json": '[{"v": 10}, {"v": 15, "t": true}]'}
        )
        self.assertEqual(
            (result["count"], result["mean"], result["change_percent"]), (2, 12.5, 50.0)
        )
        with self.assertRaises(ValueError):
            analysis.analyze_trend(None, None, {"data_json": '{"a": "x"}'})

    def test_keywords_and_redaction(self):
        result = analysis.keywords(
            None, None, {"text": "Agent tools, agent memory. 智能体 智能体", "top_k": 2}
        )
        self.assertEqual([k["keyword"] for k in result["keywords"]], ["agent", "智能体"])
        masked = analysis.redact(None, None, {"text": "mail alice@example.com or 138-1234-5678"})
        self.assertEqual(masked["text"], "mail a***@example.com or ***5678")


class DocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_markdown_edit_modes(self):
        original = "# Report\n\n## Intro\nold intro\n\n## Data\nnumbers\n"
        replaced = edit_markdown(original, "replace_section", "new intro", "Intro")
        self.assertIn("## Intro\nnew intro\n## Data\nnumbers", replaced)
        self.assertTrue(edit_markdown(original, "append", "end", None).endswith("numbers\n\nend\n"))
        self.assertEqual(edit_markdown(original, "rewrite", "all new", None), "all new\n")
        with self.assertRaises(ValueError):
            edit_markdown(original, "replace_section", "x", "Missing")

    def test_docx_write_read_and_edit(self):
        path = self.root / "r.docx"
        write_docx(path, "标题", "第一段\n第二段")
        edit_docx(path, "append", "第三段", None)
        self.assertEqual(read_text(path).splitlines(), ["标题", "第一段", "第二段", "第三段"])
        edit_docx(path, "replace_section", "新内容", "标题")
        self.assertEqual(read_text(path).splitlines(), ["标题", "新内容"])
        self.assertEqual(Document(path).paragraphs[0].style.name, "Heading 1")

    def test_pdf_with_chinese_text(self):
        path = self.root / "r.pdf"
        write_pdf(path, "中文标题", "内容\n" * 80)
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))


class ReportFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sessions = SessionStore(self.root / "data")
        self.session = self.sessions.create("medium")
        self.session.workspace = str(self.root / "reports")
        (self.root / "reports").mkdir()
        self.sessions.save(self.session)
        self.events = []
        self.context = SimpleNamespace(
            task=SimpleNamespace(metadata={"session_id": self.session.id}),
            record_activity=lambda kind, details: self.events.append(kind),
        )
        self.tools = ReportTools(self.sessions)

    def test_file_names_cannot_leave_the_folder(self):
        for name in ("../x.md", "sub/x.md", "/etc/passwd", "x.exe", ".."):
            with self.subTest(name=name), self.assertRaises((PermissionError, ValueError)):
                self.tools.read(self.context, None, {"filename": name})

    def test_save_never_overwrites_and_edit_keeps_a_backup(self):
        args = {"title": "T", "content": "c", "format": "md", "filename": "t"}
        self.assertEqual(self.tools.save(self.context, None, args)["filename"], "t.md")
        with self.assertRaises(FileExistsError):
            self.tools.save(self.context, None, args)
        result = self.tools.edit(
            self.context, None, {"filename": "t.md", "mode": "append", "content": "more"}
        )
        self.assertTrue((self.root / "reports" / result["backup"]).exists())
        listed = self.tools.list_reports(self.context, None, {})["files"]
        self.assertEqual([f["filename"] for f in listed], ["t.md"])
        self.assertEqual(self.events, ["report.saved", "report.edited"])

    def test_reports_root_limits_granted_folders(self):
        tools = ReportTools(self.sessions, reports_root=self.root / "allowed")
        self.assertEqual(
            tools.check_folder(str(self.root / "allowed" / "a")),
            (self.root / "allowed" / "a").resolve(),
        )
        for folder in (str(self.root / "elsewhere"), "relative/path", "/usr/share"):
            with self.subTest(folder=folder), self.assertRaises((PermissionError, ValueError)):
                tools.check_folder(folder)


class AdapterTests(unittest.TestCase):
    def payload(self, history=(), **extra):
        return {
            "available_tools": [
                {
                    "name": "reports.delete",
                    "description": "Delete.",
                    "input_schema": {"type": "object"},
                    "human_check": True,
                }
            ],
            "inputs": {
                "message": "hi",
                "conversation": [{"role": "user", "content": "earlier"}],
                "workspace": "/reports",
            },
            "task_memory": {"events": []},
            "knowledge": None,
            "history": list(history),
            **extra,
        }

    @staticmethod
    def prompt(payload):
        return [{"role": "system", "content": ""}, {"role": "user", "content": json.dumps(payload)}]

    def test_tool_names_and_descriptions(self):
        tools, names = native_tools(self.payload()["available_tools"])
        self.assertEqual(names, {"reports_delete": "reports.delete"})
        self.assertIn("approval", tools[0].description)
        self.assertEqual(function_name("mcp:docs:search"), "mcp_docs_search")
        clash = [{"name": n, "description": "", "input_schema": {}} for n in ("a.b", "a:b")]
        with self.assertRaises(ValueError):
            native_tools(clash)

    def test_turns_carry_state_and_a_frozen_context(self):
        replies = [
            Reply(
                tool_call=ToolCall("reports_delete", {"filename": "a.md"}),
                state={"call_id": "c1", "secret_sauce": 1},
                total_tokens=7,
            ),
            Reply(text="done"),
        ]
        turns = []
        model = SimpleNamespace(
            provider="p", model="m", decide=lambda turn: turns.append(turn) or replies.pop(0)
        )
        adapter = AgentAdapter(model, "PROMPT")

        first = adapter(self.prompt(self.payload()))
        self.assertIsInstance(first, MeteredDecision)
        self.assertEqual(first.total_tokens, 7)
        action = first.decision
        self.assertEqual(
            (action.name, action.arguments), ("tool:reports.delete", {"filename": "a.md"})
        )
        self.assertEqual(action.model_state["call_id"], "c1")
        self.assertEqual(action.model_state["context"]["workspace"], "/reports")
        self.assertTrue(turns[0].system.startswith("PROMPT"))
        self.assertEqual(turns[0].conversation, [{"role": "user", "content": "earlier"}])
        self.assertTrue(turns[0].message.startswith("hi\n\n"))

        # The folder changed meanwhile; the turn keeps the context it started with.
        later = self.payload(
            [
                {
                    "action": {"name": "tool:reports.delete", "arguments": {"filename": "a.md"}},
                    "observation": {"deleted": True},
                    "model_state": action.model_state,
                }
            ]
        )
        later["inputs"]["workspace"] = "/elsewhere"
        self.assertEqual(adapter(self.prompt(later)), NodeResult(values={"answer": "done"}))
        second = turns[1]
        self.assertEqual(second.message, turns[0].message)
        self.assertTrue(second.replay_safe)
        (step,) = second.steps
        self.assertEqual((step.tool, step.call_id), ("reports_delete", "c1"))
        self.assertEqual(json.loads(step.output), {"deleted": True})
        self.assertEqual(step.state, {"call_id": "c1", "secret_sauce": 1})

    def test_replay_is_unsafe_after_omitted_steps_or_changed_tools(self):
        turns = []
        model = SimpleNamespace(
            provider="p", model="m", decide=lambda turn: turns.append(turn) or Reply(text="")
        )
        adapter = AgentAdapter(model, "PROMPT")
        entry = {
            "action": {"name": "tool:reports.delete", "arguments": {}},
            "observation": {"error": "nope"},
            "model_state": {"call_id": "c1", "prefix": "stale"},
        }
        self.assertEqual(adapter(self.prompt(self.payload([entry]))).values["answer"], NO_ANSWER)
        self.assertFalse(turns[-1].replay_safe)
        self.assertTrue(turns[-1].steps[0].is_error)
        self.assertIsNone(turns[-1].steps[0].state.get("prefix"))

        tools, _ = native_tools(self.payload()["available_tools"])
        entry["model_state"]["prefix"] = _digest("p/m", adapter.system, tools)
        adapter(self.prompt(self.payload([entry])))
        self.assertTrue(turns[-1].replay_safe)
        adapter(self.prompt(self.payload([entry], history_omitted_older_steps=2)))
        self.assertFalse(turns[-1].replay_safe)
        model.model = "other"  # e.g. a different model chosen when approving
        adapter(self.prompt(self.payload([entry])))
        self.assertFalse(turns[-1].replay_safe)

    def test_hidden_tools_keep_their_names(self):
        model = SimpleNamespace(
            provider="p",
            model="m",
            decide=lambda turn: Reply(tool_call=ToolCall("reports_save", {}), state={}),
        )
        adapter = AgentAdapter(model, "PROMPT", known_tools=["reports.save"])
        self.assertEqual(adapter(self.prompt(self.payload())).name, "tool:reports.save")


if __name__ == "__main__":
    unittest.main()
