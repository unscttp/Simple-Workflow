"""Chat sessions: conversation, permission settings and the turn awaiting a decision.

A session is one JSON file under ``<data>/sessions/<id>/``; the tasks of its
turns are saved next to it in ``tasks/``. Session IDs are generated here and
validated on every use, so they are safe to use as directory names.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from workflow_core import Task
from workflow_memory_store import atomic_write

RISK_LEVELS = ("low", "medium", "high")
_SESSION_ID = re.compile(r"[a-f0-9]{32}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionNotFound(KeyError):
    pass


@dataclass
class Session:
    id: str
    risk_level: str
    created_at: str = field(default_factory=now)
    # The report folder the user approved for this session, if any.
    workspace: str | None = None
    # {"role": "user" | "assistant", "content": str, "task_id": str, "at": str}
    messages: list[dict] = field(default_factory=list)
    # The turn paused for a human decision; no new message is accepted meanwhile.
    pending_task_id: str | None = None

    def conversation(self, limit: int) -> list[dict]:
        """The most recent messages, as role/content pairs for the model."""
        recent = self.messages[-limit:] if limit else []
        return [{"role": m["role"], "content": m["content"]} for m in recent]


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise SessionNotFound(session_id)
        return self.root / session_id

    def create(self, risk_level: str) -> Session:
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {RISK_LEVELS}.")
        session = Session(id=uuid.uuid4().hex, risk_level=risk_level)
        self.save(session)
        return session

    def load(self, session_id: str) -> Session:
        path = self._directory(session_id) / "session.json"
        if not path.exists():
            raise SessionNotFound(session_id)
        return Session(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, session: Session) -> None:
        directory = self._directory(session.id)
        directory.mkdir(parents=True, exist_ok=True)
        data = json.dumps(asdict(session), ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write(directory / "session.json", data)

    def _task_path(self, session_id: str, task_id: str) -> Path:
        if not re.fullmatch(r"task_[0-9a-f]{12}", task_id):
            raise KeyError(task_id)
        return self._directory(session_id) / "tasks" / f"{task_id}.json"

    def save_task(self, session_id: str, task: Task) -> None:
        path = self._task_path(session_id, task.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        task.save(path)

    def load_task(self, session_id: str, task_id: str) -> Task:
        return Task.load(self._task_path(session_id, task_id))
