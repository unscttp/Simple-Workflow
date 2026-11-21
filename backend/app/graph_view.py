"""The workflow graph of a turn and where it is, for the interface.

A turn is one task of the ``chat-turn`` workflow. When its node runs a
generated graph (``graph.create``), that graph runs as a child task; the view
lists it as another graph attached to the node that created it, with the
same per-node status. Child tasks are read live from ``LiveTasks`` while the
call runs, and from the parent execution's saved graph records afterwards.

Node status: ``pending`` (not reached), ``ready``, ``running``, ``waiting``
(for the user), ``completed``, ``failed`` or ``skipped``. ``activity`` says
what a running or waiting node is doing: ``model`` (waiting for the model),
``tool`` (a tool call; ``graph`` for ``graph.create`` and ``skill.<name>.run_graph``)
or ``approval``.
"""

from __future__ import annotations

import copy

from workflow_core import Task, WorkflowDefinition

from .live import LiveTasks

STATUS = {"waiting_start": "waiting", "joined": "completed"}
ATTEMPTS = 3  # the view is read while the turn runs in another thread


def _activity(task: Task, execution_id: str) -> dict | None:
    activity = None
    for event in list(task.events):
        if event["execution_id"] != execution_id:
            continue
        kind, details = event["type"], event["details"]
        if kind == "llm.started":
            activity = {"kind": "model"}
        elif kind == "tool.requested":
            name = str(details.get("action", "")).removeprefix("tool:")
            runs_graph = name == "graph.create" or (
                name.startswith("skill.") and name.endswith(".run_graph")
            )
            activity = {"kind": "graph" if runs_graph else "tool", "tool": name}
        elif kind == "tool.waiting" and activity is not None:
            activity = {**activity, "kind": "approval"}
        elif kind in {"llm.completed", "llm.failed", "tool.completed", "tool.failed"}:
            activity = None
    return activity


def _counts(task: Task, execution_id: str) -> tuple[int, int]:
    decisions = tools = 0
    for event in list(task.events):
        if event["execution_id"] == execution_id:
            decisions += event["type"] == "llm.completed"
            tools += event["type"] in {"tool.completed", "tool.failed"}
    return decisions, tools


def _nodes(definitions: list[dict], task: Task | None) -> list[dict]:
    latest = {}
    for execution in list(task.executions) if task else []:
        latest[execution.node_id] = execution
    nodes = []
    for node in definitions:
        execution = latest.get(node["id"])
        view = {
            "id": node["id"],
            "type": node.get("type"),
            "name": node.get("name") or node["id"],
            "allowance": node.get("allowance"),
            "status": "pending",
            "attempts": 0,
            "decisions": 0,
            "tool_calls": 0,
            "activity": None,
        }
        if execution is not None:
            status = STATUS.get(execution.status, execution.status)
            decisions, tools = _counts(task, execution.id)
            view.update(
                status=status,
                attempts=execution.attempt,
                decisions=decisions,
                tool_calls=tools,
                error=execution.error,
            )
            if status in {"running", "waiting"}:
                view["activity"] = _activity(task, execution.id) or (
                    {"kind": "approval"} if status == "waiting" else {"kind": "model"}
                )
        nodes.append(view)
    return nodes


def _edges(definitions: list[dict]) -> list[dict]:
    return [
        {"from": edge["from"], "to": edge["to"], "label": edge.get("label") or None}
        for edge in definitions
    ]


def _graph_status(record: dict, child: Task | None) -> str:
    if record.get("error"):
        return "failed"
    if record.get("result") is not None:
        return "completed"
    if not record.get("approved"):
        return "awaiting_review"
    if child is None:
        return "starting"
    return "running" if child.status == "running" else child.status


def _children(task: Task, graph_id: str, live: LiveTasks, depth: int) -> list[dict]:
    """Generated graphs created by the nodes of ``task``, depth first."""
    graphs = []
    for execution in list(task.executions):
        records = copy.deepcopy(execution.memory_state.get("graphs", {}))
        live_children = {child.workflow_id: child for child in live.children(task.id, execution.id)}
        for record in records.values():
            child = live_children.get(record["id"])
            if child is None and "task" in record:
                child = Task.from_dict(record["task"])
            graph = record["graph"]
            graphs.append(
                {
                    "id": record["id"],
                    "title": "Generated graph",
                    "parent": {"graph": graph_id, "node": execution.node_id},
                    "depth": depth,
                    "explanation": record.get("explanation"),
                    "skill": (record.get("skill") or {}).get("name"),
                    # The saved graph this run reused, and whether this run was saved.
                    "skill_graph": record.get("skill_graph"),
                    "saved": record.get("save_outcome"),
                    "status": _graph_status(record, child),
                    "start": record.get("start", []),
                    "nodes": _nodes(graph["nodes"], child),
                    "edges": _edges(graph["edges"]),
                }
            )
            if child is not None:
                graphs.extend(_children(child, record["id"], live, depth + 1))
    return graphs


def _build(workflow: WorkflowDefinition, task: Task | None, live: LiveTasks, running: bool):
    definition = workflow.to_dict()
    root = {
        "id": workflow.id,
        "title": workflow.name,
        "parent": None,
        "depth": 0,
        "explanation": definition.get("description") or None,
        "skill": None,
        "skill_graph": None,
        "saved": None,
        "status": "idle" if task is None else task.status,
        "start": list(workflow.start),
        "nodes": _nodes(definition["nodes"], task),
        "edges": _edges(definition["edges"]),
    }
    graphs = [root] + (_children(task, workflow.id, live, 1) if task else [])
    current = [
        {"graph": graph["id"], "node": node["id"], "status": node["status"], **node["activity"]}
        for graph in graphs
        for node in graph["nodes"]
        if node["activity"]
    ]
    return {
        "task_id": task.id if task else None,
        "status": root["status"],
        "running": running,
        "graphs": graphs,
        "current": current,
    }


def task_graph(
    workflow: WorkflowDefinition, task: Task | None, live: LiveTasks, *, running: bool = False
) -> dict:
    for attempt in range(ATTEMPTS):
        try:
            return _build(workflow, task, live, running)
        except RuntimeError:  # a dict changed size while the turn wrote to it
            if attempt == ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")
