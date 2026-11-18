"""Chat turns run as NLG tasks.

Each user message starts one task of the ``chat-turn`` workflow (one LLM node).
The node's allowance follows the session's risk level, its tools come from
``app.tools`` and it can read and propose long-term knowledge. When a tool
needs the user's approval the task pauses; it is saved with the session and
resumed by ``decide`` once the user approves or rejects.

A new engine is built for every request: it holds the model client for that
request's API key and nothing else of the user's.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from workflow_core import (
    AllowanceRegistry,
    KnowledgeStore,
    Task,
    WorkflowDefinition,
    WorkflowEngine,
    resolve_human_check,
)

from . import journal
from .model import AgentAdapter, ChatModel, KnowledgeWriterAdapter
from .sessions import RISK_LEVELS, Session, SessionStore, now
from .settings import CONFIG_DIR, PROMPT_FILE, Settings
from .tools import TRUSTED_ACTIONS, build_tools
from .tools.research import SearchFunction, duckduckgo_search

ModelFactory = Callable[[str, str], ChatModel]
NODE_TYPE = "assistant"
REJECTED_NOTE = "（你拒绝了该操作，本轮任务已结束。）"


class TurnInProgress(RuntimeError):
    """A turn of this session is waiting for an approval decision."""


class NoPendingTurn(RuntimeError):
    """There is no paused turn to decide on."""


@dataclass
class TurnResult:
    status: str  # completed / waiting / failed
    task_id: str
    answer: str | None = None
    error: str | None = None
    tool_logs: list[dict] = field(default_factory=list)
    audit_entries: list[dict] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)


class AgentService:
    def __init__(
        self,
        settings: Settings,
        model_factory: ModelFactory,
        *,
        search: SearchFunction = duckduckgo_search,
    ) -> None:
        self.settings = settings
        self.model_factory = model_factory
        self.sessions = SessionStore(settings.data_dir)
        self.knowledge = KnowledgeStore(Path(settings.data_dir) / "knowledge")
        self.tools = build_tools(self.sessions, reports_root=settings.reports_root, search=search)
        self.allowances = AllowanceRegistry()
        for path in sorted((CONFIG_DIR / "allowances").glob("*.json")):
            self.allowances.load(path)
        self.workflow_document = json.loads((CONFIG_DIR / "workflow.json").read_text("utf-8"))
        self.system_prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
        self.known_tools = [tool.name for tool in self.tools.list_tools()] + [
            f"knowledge.{op}" for op in ("list", "read", "search", "propose")
        ]
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- building blocks -----------------------------------------------------

    def lock(self, session_id: str) -> threading.Lock:
        """Turns of one session run one at a time."""
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def workflow(self, session: Session) -> WorkflowDefinition:
        workflow = WorkflowDefinition.from_dict(self.workflow_document)
        workflow.node(NODE_TYPE).allowance = f"risk-{session.risk_level}"
        workflow.assert_valid()
        return workflow

    def engine(self, model: ChatModel) -> WorkflowEngine:
        engine = WorkflowEngine(
            self.allowances,
            tools=self.tools,
            knowledge=self.knowledge,
            memory_settings_path=CONFIG_DIR / "memory_settings.json",
        )
        engine.knowledge_trusted_actions |= TRUSTED_ACTIONS
        # Each turn is a short task: active-memory notes would only add model calls.
        adapter = AgentAdapter(model, self.system_prompt, self.known_tools)
        engine.register_llm(NODE_TYPE, adapter, memory_writer=False)
        if self.settings.knowledge_consolidation:
            engine.register_knowledge_writer(KnowledgeWriterAdapter(model))
        return engine

    # -- sessions ------------------------------------------------------------

    def create_session(self) -> Session:
        return self.sessions.create(self.settings.default_risk_level)

    def set_risk_level(self, session_id: str, risk_level: str) -> Session:
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {RISK_LEVELS}.")
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            session.risk_level = risk_level
            self.sessions.save(session)
            return session

    def revoke_workspace(self, session_id: str) -> Session:
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            session.workspace = None
            self.sessions.save(session)
            return session

    def pending(self, session: Session) -> list[dict]:
        if not session.pending_task_id:
            return []
        return journal.pending_approvals(
            self.sessions.load_task(session.id, session.pending_task_id)
        )

    # -- turns ---------------------------------------------------------------

    def send_message(self, session_id: str, text: str, api_key: str, model_name: str) -> TurnResult:
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            if session.pending_task_id:
                raise TurnInProgress("Decide on the pending approval before sending a message.")
            model = self.model_factory(api_key, model_name)
            workflow = self.workflow(session)
            task = Task.create(
                workflow,
                {
                    "message": text,
                    "conversation": session.conversation(self.settings.history_messages),
                    "workspace": session.workspace,
                },
            )
            task.metadata["session_id"] = session.id
            session.messages.append(
                {"role": "user", "content": text, "task_id": task.id, "at": now()}
            )
            self.sessions.save(session)
            return self._run(session.id, workflow, task, self.engine(model))

    def decide(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        api_key: str,
        model_name: str,
        comment: str = "",
    ) -> TurnResult:
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            if not session.pending_task_id:
                raise NoPendingTurn("No turn is waiting for a decision.")
            task = self.sessions.load_task(session.id, session.pending_task_id)
            resolve_human_check(
                task, request_id, approved, reviewer=f"session:{session.id}", comment=comment
            )
            model = self.model_factory(api_key, model_name)
            return self._run(session.id, self.workflow(session), task, self.engine(model))

    def _run(self, session_id: str, workflow, task: Task, engine: WorkflowEngine) -> TurnResult:
        engine.task_pool.add(task)
        while ready := engine.task_pool.ready():
            for ready_task, execution in ready:
                engine.run_execution(workflow, ready_task, execution)
        if task.status == "failed" and not task.metadata.get("knowledge_consolidated"):
            engine.consolidate_knowledge(workflow, task)

        # Tools may have changed the session (an approved folder), so reload it.
        session = self.sessions.load(session_id)
        self.sessions.save_task(session.id, task)
        result = TurnResult(
            status="completed",
            task_id=task.id,
            tool_logs=journal.tool_logs(task),
            audit_entries=journal.audit_entries(task),
            pending_approvals=journal.pending_approvals(task),
        )
        if result.pending_approvals:
            result.status = "waiting"
            session.pending_task_id = task.id
        else:
            session.pending_task_id = None
            if task.status == "completed":
                result.answer = task.data["answer"]
            else:
                result.status = "failed"
                result.error = self._failure(task)
                result.answer = REJECTED_NOTE if self._rejected(task) else None
            if result.answer:
                session.messages.append(
                    {"role": "assistant", "content": result.answer, "task_id": task.id, "at": now()}
                )
        self.sessions.save(session)
        return result

    @staticmethod
    def _rejected(task: Task) -> bool:
        return any(
            request["status"] == "rejected"
            for execution in task.executions
            for request in execution.human_checks
        )

    @staticmethod
    def _failure(task: Task) -> str:
        errors = [e.error for e in task.executions if e.status == "failed" and e.error]
        return errors[-1] if errors else "The turn failed."
