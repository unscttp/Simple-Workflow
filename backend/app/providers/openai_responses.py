"""OpenAI Responses API (GPT models).

Requests are stateless (``store=False``). Each tool call's output items - the
encrypted reasoning items and the function call - are kept as step state and
sent back unchanged on the next request, so the model continues with its
earlier reasoning instead of starting over after every tool result.
"""

from __future__ import annotations

import json
from typing import Any

from .base import Reply, ToolCall, Turn, parse_arguments, parse_json_object
from .chat_completions import JSON_NOTE, wrap_openai_errors

REPLAYED_ITEM_TYPES = ("reasoning", "function_call")


class ResponsesModel:
    provider = "openai"

    def __init__(self, model: str, client: Any) -> None:
        self.model = model
        self._client = client

    def __repr__(self) -> str:
        return f"ResponsesModel(model={self.model!r})"

    def _create(self, **request):
        return wrap_openai_errors(
            lambda: self._client.responses.create(model=self.model, store=False, **request),
            secrets=(getattr(self._client, "api_key", None),),
        )

    @staticmethod
    def input_items(turn: Turn) -> list[dict]:
        items: list[dict] = [*turn.conversation, {"role": "user", "content": turn.message}]
        for step in turn.steps:
            stored = (step.state or {}).get("items")
            if stored and turn.replay_safe:
                items.extend(stored)
            else:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": step.call_id,
                        "name": step.tool,
                        "arguments": json.dumps(step.arguments, ensure_ascii=False),
                    }
                )
            items.append(
                {"type": "function_call_output", "call_id": step.call_id, "output": step.output}
            )
        return items

    def decide(self, turn: Turn) -> Reply:
        response = self._create(
            instructions=turn.system,
            input=self.input_items(turn),
            tools=[
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    # Our schemas have optional fields, which strict mode does not allow.
                    "strict": False,
                }
                for tool in turn.tools
            ],
            parallel_tool_calls=False,
            include=["reasoning.encrypted_content"],
        )
        usage = getattr(response, "usage", None)
        reply = Reply(
            text=response.output_text or "", total_tokens=getattr(usage, "total_tokens", None)
        )
        kept = []
        for item in response.output:
            if item.type in REPLAYED_ITEM_TYPES:
                kept.append(item.model_dump(mode="json", exclude_none=True))
            if item.type == "function_call":
                reply.tool_call = ToolCall(item.name, parse_arguments(item.arguments))
                reply.state = {"call_id": item.call_id, "items": kept}
                break
        return reply

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._create(
            instructions=system + JSON_NOTE,
            input=[{"role": "user", "content": user}],
            text={"format": {"type": "json_object"}},
        )
        return parse_json_object(response.output_text or "")
