"""The provider-neutral view of one model decision, shared by every provider.

``AgentAdapter`` (app/model.py) turns an NLG prompt into a ``Turn``; each
provider renders the ``Turn`` in its own API's format and returns a ``Reply``.
Whatever a provider needs to continue after a tool call (reasoning items,
thinking blocks, thought signatures) goes into ``Reply.state``; NLG stores it
with the step and it comes back as ``Step.state`` on the next decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

_FUNCTION_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def function_name(tool_name: str) -> str:
    """Function names accepted by every provider: letters, digits, '_' and '-'."""
    return _FUNCTION_NAME.sub("_", tool_name)[:64]


@dataclass
class ToolSpec:
    name: str  # function name, as the model sees it
    description: str
    parameters: dict


@dataclass
class Step:
    """One earlier action of this turn and its result."""

    tool: str  # function name
    arguments: dict
    call_id: str
    output: str  # JSON text of the observation
    is_error: bool = False
    state: dict | None = None


@dataclass
class Turn:
    system: str
    conversation: list[dict]  # earlier chat messages: {"role", "content"}
    message: str  # the current user message, with this turn's context
    tools: list[ToolSpec]
    steps: list[Step] = field(default_factory=list)
    # False when earlier steps were left out of the history or the system prompt or
    # tools changed during the turn: providers must then not replay reasoning that
    # is bound to the exact earlier conversation (Claude thinking blocks).
    replay_safe: bool = True


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_call: ToolCall | None = None
    state: dict | None = None
    total_tokens: int | None = None


class ModelError(RuntimeError):
    """A model request failed. The message never includes credentials."""


_KEY_LIKE = re.compile(r"(sk-ant-[\w-]+|sk-[\w-]{8,}|AIza[\w-]{20,}|Bearer\s+\S+)")
MAX_ERROR_CHARS = 300


def provider_message(body, secrets=()) -> str:
    """The provider's own error message from an error body, with keys removed.

    Providers say why a request was refused ("API key not valid", "Invalid
    value at tools[0]..."); showing that is what makes a 400 fixable. Every
    known secret and anything shaped like an API key is replaced by "[key]".
    """
    message = ""
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(error, str):
            message = error
    elif isinstance(body, list) and body:
        return provider_message(body[0], secrets)
    elif isinstance(body, str):
        message = body
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[key]")
    message = _KEY_LIKE.sub("[key]", " ".join(message.split()))
    if len(message) > MAX_ERROR_CHARS:
        message = message[: MAX_ERROR_CHARS - 1] + "…"
    return message


class ChatModel(Protocol):
    provider: str
    model: str

    def decide(self, turn: Turn) -> Reply: ...

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


def parse_arguments(raw: str | None) -> dict:
    """Tool arguments as an object; malformed JSON is passed on for schema checks to reject."""
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"invalid_json": raw}
    return arguments if isinstance(arguments, dict) else {"invalid_arguments": arguments}


def parse_json_object(text: str) -> dict:
    """The JSON object in a reply, tolerating a Markdown code fence around it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        reply = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ModelError("The model did not return valid JSON.") from exc
    if not isinstance(reply, dict):
        raise ModelError("The model did not return a JSON object.")
    return reply


# What the knowledge writer must return (NLG knowledge consolidation). Written in
# the subset every provider's structured-output mode accepts.
KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "create_page",
                            "set_summary",
                            "add_entry",
                            "update_entry",
                            "retire_entry",
                        ],
                    },
                    "page": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "text": {"type": "string"},
                    "entry": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["op", "page"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["operations", "rationale", "evidence"],
    "additionalProperties": False,
}
