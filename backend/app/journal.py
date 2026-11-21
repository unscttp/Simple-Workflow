"""Views of a turn's task journal for the interface: tool calls, audit trail, approvals."""

from __future__ import annotations

import json

from workflow_core import Task, pending_human_checks

AUDIT_EVENT_TYPES = {
    "approval.requested": "Approval requested",
    "approval.resolved": "Approval decided",
    "security.alert": "Security alert",
    "workspace.granted": "Report folder approved",
    "report.read": "Report read",
    "report.saved": "Report saved",
    "report.edited": "Report edited",
    "report.deleted": "Report deleted",
    "knowledge.read": "Knowledge page read",
    "knowledge.proposed": "Knowledge change proposed",
    "knowledge.consolidation_failed": "Knowledge consolidation failed",
    "node.timed_out": "Turn timed out",
    "skill.invoke.opened": "Skill opened",
    "skill.invoke.closed": "Skill closed",
    "skill.script.exited": "Skill script ran",
    "graph.created": "Generated graph approved",
    "graph.completed": "Generated graph finished",
    "graph.failed": "Generated graph failed",
    "skill.graph.saved": "Graph saved to skill",
}


def _output(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def tool_logs(task: Task) -> list[dict]:
    """One entry per tool call of the turn, in order.

    A call that paused for approval keeps its own entry, marked approved or
    rejected once the user decides; an approved call then appears again as the
    call that actually ran.
    """
    calls: dict[str, dict] = {}
    waiting: dict[str, dict] = {}
    for event in task.events:
        kind, details = event["type"], event["details"]
        if kind == "tool.requested":
            calls[event["id"]] = {
                "id": event["id"],
                "timestamp": event["timestamp"],
                "tool_name": str(details.get("action", "")).removeprefix("tool:"),
                "arguments": details.get("arguments"),
                "status": "requested",
                "output": "",
            }
        elif kind == "tool.replayed":
            calls[event["id"]] = {
                "id": event["id"],
                "timestamp": event["timestamp"],
                "tool_name": details["action"].removeprefix("tool:"),
                "arguments": details["arguments"],
                "status": "completed",
                "output": _output(details["result"]),
            }
        elif kind in {"tool.completed", "tool.failed", "tool.waiting"}:
            call = calls.get(details.get("call_id"))
            if call is None:
                continue
            if kind == "tool.completed":
                call.update(status="completed", output=_output(details["result"]))
            elif kind == "tool.failed":
                call.update(status="failed", output=f"{details['error_type']}: {details['error']}")
            else:
                call.update(status="waiting", output="Waiting for the user's approval.")
                waiting[details.get("request_id")] = call
        elif kind == "approval.resolved" and details.get("request_id") in waiting:
            approved = details.get("approved")
            waiting.pop(details["request_id"]).update(
                status="approved" if approved else "rejected",
                output=f"{'Approved' if approved else 'Rejected'} by the user.",
            )
    return list(calls.values())


def _summary(kind: str, details: dict) -> str:
    title = AUDIT_EVENT_TYPES[kind]
    if kind == "approval.resolved":
        return f"{title}: {'approved' if details.get('approved') else 'rejected'}"
    for key in ("filename", "folder", "page", "message", "error", "path", "graph_id", "skill"):
        if details.get(key) and isinstance(details[key], str):
            return f"{title}: {details[key]}"
    if kind == "knowledge.proposed":
        return f"{title}: {', '.join(details.get('pages', []))} ({details.get('status')})"
    return title


def audit_entries(task: Task) -> list[dict]:
    return [
        {
            "timestamp": event["timestamp"],
            "type": event["type"],
            "summary": _summary(event["type"], event["details"]),
        }
        for event in task.events
        if event["type"] in AUDIT_EVENT_TYPES
    ]


def pending_approvals(task: Task) -> list[dict]:
    """Requests the user must decide on, with what they would allow.

    Approvals needed inside a generated graph are forwarded to the turn as
    ``child_graph_review``; they are shown as the request they wrap, with
    ``via_graph`` naming the graph.
    """
    approvals = []
    for request in pending_human_checks(task):
        details = request["details"]
        via_graph = None
        if details.get("kind") == "child_graph_review":
            via_graph = details.get("graph_id")
            details = details["child_request"]["details"]
        tool = details.get("tool") or {}
        graph = details.get("graph")
        approvals.append(
            {
                "id": request["id"],
                "kind": details.get("kind", "custom"),
                "tool_name": tool.get("name"),
                "description": tool.get("description", ""),
                "risk_level": tool.get("risk_level"),
                "arguments": details.get("arguments", {}),
                "safety_context": details.get("safety_context"),
                "via_graph": via_graph,
                "explanation": details.get("explanation"),
                "skill": (details.get("skill") or {}).get("name"),
                "graph": None
                if graph is None
                else {
                    "nodes": [
                        {
                            "id": node["id"],
                            "name": node.get("name") or node["id"],
                            "allowance": node.get("allowance"),
                        }
                        for node in graph["nodes"]
                    ],
                    "edges": [{"from": e["from"], "to": e["to"]} for e in graph["edges"]],
                },
                "path": details.get("path"),
            }
        )
    return approvals
