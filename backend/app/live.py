"""Tasks that are running right now, as reported by NLG run observers.

``AgentService`` registers ``LiveTasks.observe`` on every engine it builds.
NLG calls it before each node execution, including the executions of
generated child graphs, which run on their own tasks inside the parent's
``graph.create`` call. That lets the graph view follow a turn while it runs,
and lets tools find the session of a child task: child tasks carry the parent
task's id in their metadata, never the session id.
"""

from __future__ import annotations

import threading

from workflow_core import Task


class LiveTasks:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._guard = threading.Lock()

    def observe(self, task: Task, execution) -> None:
        with self._guard:
            self._tasks[task.id] = task

    def get(self, task_id: str) -> Task | None:
        with self._guard:
            return self._tasks.get(task_id)

    def children(self, task_id: str, execution_id: str | None = None) -> list[Task]:
        """Live child tasks of a task (optionally of one of its executions)."""
        with self._guard:
            tasks = list(self._tasks.values())
        return [
            task
            for task in tasks
            if task.metadata.get("parent_task_id") == task_id
            and (execution_id is None or task.metadata.get("parent_execution_id") == execution_id)
        ]

    def session_of(self, task: Task) -> str:
        """The session a task belongs to, following child tasks up to their root."""
        seen = set()
        while "session_id" not in task.metadata:
            parent_id = task.metadata.get("parent_task_id")
            parent = self.get(parent_id) if parent_id else None
            if parent is None or parent.id in seen:
                raise LookupError("The task is not part of a running turn.")
            seen.add(parent.id)
            task = parent
        return task.metadata["session_id"]

    def root_of(self, task: Task) -> str:
        """The id of the turn a task belongs to (itself, or the root of its child chain)."""
        seen = set()
        while "session_id" not in task.metadata:
            parent_id = task.metadata.get("parent_task_id")
            parent = self.get(parent_id) if parent_id else None
            if parent is None or parent.id in seen:
                break
            seen.add(parent.id)
            task = parent
        return task.id

    def release(self, root_id: str) -> None:
        """Forget a finished or paused turn: its task and every descendant."""
        with self._guard:
            doomed, frontier = set(), {root_id}
            while frontier:
                doomed |= frontier
                frontier = {
                    task.id
                    for task in self._tasks.values()
                    if task.metadata.get("parent_task_id") in frontier and task.id not in doomed
                }
            for task_id in doomed:
                self._tasks.pop(task_id, None)
