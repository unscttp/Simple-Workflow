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
from .graph_view import task_graph
from .live import LiveTasks
from .model import AgentAdapter, KnowledgeWriterAdapter
from .providers import ChatModel, Provider, create_model
from .sessions import RISK_LEVELS, Session, SessionStore, now
from .settings import CONFIG_DIR, PROMPT_FILE, Settings
from .tools import TRUSTED_ACTIONS, build_tools
from .tools.research import SearchFunction, duckduckgo_search

ModelFactory = Callable[[Provider, str, str], ChatModel]
NODE_TYPE = "assistant"
REJECTED_NOTE = "（你拒绝了该操作，本轮任务已结束。）"


class TurnInProgress(RuntimeError):
    """A turn of this session is waiting for an approval decision."""


class NoPendingTurn(RuntimeError):
    """There is no paused turn to decide on."""


@dataclass(frozen=True)
class ModelChoice:
    """The provider, key and model the user picked for one request."""

    provider: str
    api_key: str
    model: str | None = None


@dataclass
class TurnResult:
    status: str  # completed / waiting / failed
    task_id: str
    answer: str | None = None
    error: str | None = None
    tool_logs: list[dict] = field(default_factory=list)
    audit_entries: list[dict] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)
    # Which models ran the turn and what it took: decisions, tokens, tool calls.
    stats: dict = field(default_factory=dict)


def turn_stats(task: Task) -> dict:
    decisions = [e["details"] for e in task.events if e["type"] == "llm.completed"]
    return {
        "models": task.metadata.get("models", []),
        "decisions": len(decisions),
        "tokens": sum(d.get("tokens", 0) for d in decisions),
        "tokens_estimated": any(d.get("meter") == "estimated" for d in decisions),
        # Finished calls: an approved call is requested again when the turn resumes.
        "tool_calls": sum(e["type"] in {"tool.completed", "tool.failed"} for e in task.events),
    }


class AgentService:
    def __init__(
        self,
        settings: Settings,
        model_factory: ModelFactory = create_model,
        *,
        search: SearchFunction = duckduckgo_search,
    ) -> None:
        self.settings = settings
        self.model_factory = model_factory
        self.sessions = SessionStore(settings.data_dir)
        self.knowledge = KnowledgeStore(Path(settings.data_dir) / "knowledge")
        # Running turns and their generated-graph child tasks, for tools and the graph view.
        self.live = LiveTasks()
        self._running: dict[str, str] = {}  # session id -> id of the task running now
        self.tools = build_tools(
            self.sessions,
            reports_root=settings.reports_root,
            search=search,
            session_of=self.live.session_of,
        )
        self.allowances = AllowanceRegistry()
        for path in sorted((CONFIG_DIR / "allowances").glob("*.json")):
            self.allowances.load(path)
        self.workflow_document = json.loads((CONFIG_DIR / "workflow.json").read_text("utf-8"))
        self.system_prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
        self.known_tools = [tool.name for tool in self.tools.list_tools()] + [
            f"knowledge.{op}" for op in ("list", "read", "search", "propose")
        ]
        self.known_tools.append("graph.create")
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
        engine.add_run_observer(self.live.observe)
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

    def model(self, choice: ModelChoice) -> ChatModel:
        provider = self.settings.providers.get(choice.provider)
        if provider is None:
            raise ValueError(f"Unknown model provider {choice.provider!r}.")
        return self.model_factory(provider, choice.api_key, choice.model or provider.default_model)

    def send_message(self, session_id: str, text: str, choice: ModelChoice) -> TurnResult:
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            if session.pending_task_id:
                raise TurnInProgress("Decide on the pending approval before sending a message.")
            model = self.model(choice)
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
            return self._run(session.id, workflow, task, model)

    def decide(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        choice: ModelChoice,
        comment: str = "",
    ) -> TurnResult:
        with self.lock(session_id):
            session = self.sessions.load(session_id)
            if not session.pending_task_id:
                raise NoPendingTurn("No turn is waiting for a decision.")
            task = self.sessions.load_task(session.id, session.pending_task_id)
            model = self.model(choice)
            resolve_human_check(
                task, request_id, approved, reviewer=f"session:{session.id}", comment=comment
            )
            return self._run(session.id, self.workflow(session), task, model)

    def _run(self, session_id: str, workflow, task: Task, model: ChatModel) -> TurnResult:
        engine = self.engine(model)
        label = {"provider": model.provider, "model": model.model}
        if label not in task.metadata.setdefault("models", []):
            task.metadata["models"].append(label)
        engine.task_pool.add(task)
        self._running[session_id] = task.id
        try:
            while ready := engine.task_pool.ready():
                for ready_task, execution in ready:
                    engine.run_execution(workflow, ready_task, execution)
            if task.status == "failed" and not task.metadata.get("knowledge_consolidated"):
                engine.consolidate_knowledge(workflow, task)
        finally:
            self._running.pop(session_id, None)
            self.live.release(task.id)

        # Tools may have changed the session (an approved folder), so reload it.
        session = self.sessions.load(session_id)
        self.sessions.save_task(session.id, task)
        result = TurnResult(
            status="completed",
            task_id=task.id,
            tool_logs=journal.tool_logs(task),
            audit_entries=journal.audit_entries(task),
            pending_approvals=journal.pending_approvals(task),
            stats=turn_stats(task),
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
                    {
                        "role": "assistant",
                        "content": result.answer,
                        "task_id": task.id,
                        "at": now(),
                        "model": f"{model.provider}/{model.model}",
                    }
                )
        self.sessions.save(session)
        return result

    # -- graph view ------------------------------------------------------------

    def graph(self, session_id: str, task_id: str | None = None) -> dict:
        """The workflow graph of a turn and where it is: the running turn, the
        turn waiting for approval, or the latest finished turn."""
        session = self.sessions.load(session_id)
        task_id = task_id or self._running.get(session_id) or session.pending_task_id
        if task_id is None:
            task_id = next(
                (m["task_id"] for m in reversed(session.messages) if m.get("task_id")), None
            )
        task = self.live.get(task_id) if task_id else None
        if task is not None and task.metadata.get("session_id") != session.id:
            task = None  # only root turns of this session, never another session's task
        if task is None and task_id:
            try:
                task = self.sessions.load_task(session.id, task_id)
            except (FileNotFoundError, KeyError, ValueError):
                task = None
        return task_graph(
            self.workflow(session),
            task,
            self.live,
            running=task is not None and self._running.get(session_id) == task.id,
        )

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
