"""Claude through the Anthropic Messages API.

* Thinking runs with each model's defaults (adaptive on current models).
  The assistant content of each tool call - thinking blocks included - is kept
  as step state and replayed unchanged. Thinking blocks are bound to the exact
  earlier conversation, so they are replayed only while the turn's prefix is
  unchanged (``Turn.replay_safe``); otherwise all of them are left out, which
  the API accepts.
* One tool call per response (``disable_parallel_tool_use``), because NLG runs
  one action per decision and every ``tool_use`` needs a ``tool_result``.
* On models with server-side refusal fallbacks, ``fallbacks: "default"`` lets
  the API retry a declined request on a fallback model within the same call.
* The prompt prefix is cached automatically (top-level ``cache_control``).
* Knowledge consolidation uses structured output (``output_config.format``).
"""

from __future__ import annotations

from typing import Any

from .base import (
    ModelError,
    Reply,
    ToolCall,
    Turn,
    parse_json_object,
    provider_message,
    request_failed,
)

MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks.
FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
THINKING_BLOCK_TYPES = {"thinking", "redacted_thinking"}
PLAIN_BLOCK_TYPES = {"text", "tool_use"}


def _wrap_errors(call, secrets=()):
    import anthropic

    try:
        return call()
    except anthropic.APIStatusError as exc:
        raise request_failed(exc.status_code, provider_message(exc.body, secrets)) from None
    except anthropic.APITimeoutError:
        raise request_failed(None, "the request timed out") from None
    except anthropic.APIConnectionError:
        raise request_failed(None, "could not connect") from None
    except anthropic.APIError as exc:
        raise ModelError(f"Model request failed: {type(exc).__name__}.") from None


class ClaudeModel:
    provider = "anthropic"

    def __init__(self, model: str, client: Any) -> None:
        self.model = model
        self._client = client
        self._secrets = (getattr(client, "api_key", None),)

    def __repr__(self) -> str:
        return f"ClaudeModel(model={self.model!r})"

    def _create(self, **request):
        request.update(model=self.model, max_tokens=MAX_TOKENS, cache_control={"type": "ephemeral"})
        if self.model in FALLBACK_MODELS:
            return _wrap_errors(
                lambda: self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **request
                ),
                self._secrets,
            )
        return _wrap_errors(lambda: self._client.messages.create(**request), self._secrets)

    @staticmethod
    def _check_stop(response) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise ModelError(f"The model declined this request (refusal: {category}).")

    @staticmethod
    def messages(turn: Turn) -> list[dict]:
        messages = [*turn.conversation, {"role": "user", "content": turn.message}]
        for step in turn.steps:
            content = (step.state or {}).get("content")
            if content and turn.replay_safe:
                assistant = content
            elif content:
                assistant = [block for block in content if block.get("type") in PLAIN_BLOCK_TYPES]
            else:
                assistant = [
                    {
                        "type": "tool_use",
                        "id": step.call_id,
                        "name": step.tool,
                        "input": step.arguments,
                    }
                ]
            messages.append({"role": "assistant", "content": assistant})
            result = {"type": "tool_result", "tool_use_id": step.call_id, "content": step.output}
            if step.is_error:
                result["is_error"] = True
            messages.append({"role": "user", "content": [result]})
        return messages

    def decide(self, turn: Turn) -> Reply:
        request: dict[str, Any] = {"system": turn.system, "messages": self.messages(turn)}
        if turn.tools:
            request["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in turn.tools
            ]
            request["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        response = self._create(**request)
        self._check_stop(response)
        usage = response.usage
        tokens = sum(
            getattr(usage, name, None) or 0
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
        reply = Reply(text=text, total_tokens=tokens)
        call = next((block for block in response.content if block.type == "tool_use"), None)
        if call is not None:
            reply.tool_call = ToolCall(call.name, dict(call.input))
            reply.state = {
                "call_id": call.id,
                "content": [
                    block.model_dump(mode="json", exclude_none=True) for block in response.content
                ],
            }
        elif response.stop_reason == "max_tokens" and not text:
            raise ModelError("The reply was cut off before it finished.")
        return reply

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._create(
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        self._check_stop(response)
        text = next((block.text for block in response.content if block.type == "text"), "")
        return parse_json_object(text)
