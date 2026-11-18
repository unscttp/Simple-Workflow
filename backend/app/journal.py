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
}


def _output(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def tool_logs(task: Task) -> list[dict]:
    """One entry per tool call of the turn, in order."""
    calls: dict[str, dict] = {}
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
    return list(calls.values())


def _summary(kind: str, details: dict) -> str:
    title = AUDIT_EVENT_TYPES[kind]
    if kind == "approval.resolved":
        return f"{title}: {'approved' if details.get('approved') else 'rejected'}"
    for key in ("filename", "folder", "page", "message", "error"):
        if details.get(key):
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
    """Requests the user must decide on, with what they would allow."""
    approvals = []
    for request in pending_human_checks(task):
        details = request["details"]
        tool = details.get("tool") or {}
        approvals.append(
            {
                "id": request["id"],
                "kind": details.get("kind", "custom"),
                "tool_name": tool.get("name"),
                "description": tool.get("description", ""),
                "risk_level": tool.get("risk_level"),
                "arguments": details.get("arguments", {}),
                "safety_context": details.get("safety_context"),
            }
        )
    return approvals
