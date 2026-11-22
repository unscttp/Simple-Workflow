"""Chat Completions API: DeepSeek, and Gemini through Google's OpenAI-compatible endpoint.

Two pieces of state are carried between tool calls of a turn when the
provider returns them:

* Gemini attaches a thought signature to each tool call
  (``extra_content.google.thought_signature``) and rejects a later request
  whose tool-call history lacks it;
* DeepSeek thinking models return ``reasoning_content``, which is sent back
  on the assistant tool-call message of the same turn.

For Gemini, tool schemas are converted to the subset Gemini accepts
(``gemini_schema``), tool results carry the function name, and tool-call
messages carry no empty text. Errors include the provider's reason with keys
removed, so a refused request says why.
"""

from __future__ import annotations

import json
from typing import Any

from .base import Reply, ToolCall, Turn, parse_arguments, parse_json_object, provider_message
from .gemini_schema import gemini_parameters

JSON_NOTE = "\n\nRespond with a single JSON object and nothing else."


def wrap_openai_errors(call, secrets=(), what: str = "Model request"):
    """Run an OpenAI SDK call; failures become ModelError with the provider's
    reason, keys removed (the SDK's own exception is not chained)."""
    import openai

    from .base import ModelError

    try:
        return call()
    except openai.APIStatusError as exc:
        reason = provider_message(exc.body, secrets)
        detail = f": {reason}" if reason else "."
        raise ModelError(f"{what} failed with HTTP {exc.status_code}{detail}") from None
    except openai.APIError as exc:
        raise ModelError(f"{what} failed: {type(exc).__name__}.") from None


class ChatCompletionsModel:
    def __init__(
        self,
        provider: str,
        model: str,
        client: Any,
        *,
        temperature: float | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._client = client
        self._temperature = temperature

    def __repr__(self) -> str:
        return f"ChatCompletionsModel(provider={self.provider!r}, model={self.model!r})"

    def _create(self, **request):
        if self._temperature is not None:
            request["temperature"] = self._temperature
        return wrap_openai_errors(
            lambda: self._client.chat.completions.create(model=self.model, **request),
            secrets=(getattr(self._client, "api_key", None),),
        )

    def messages(self, turn: Turn) -> list[dict]:
        messages = [{"role": "system", "content": turn.system}, *turn.conversation]
        messages.append({"role": "user", "content": turn.message})
        for step in turn.steps:
            state = step.state or {}
            call = {
                "id": step.call_id,
                "type": "function",
                "function": {
                    "name": step.tool,
                    "arguments": json.dumps(step.arguments, ensure_ascii=False),
                },
            }
            if state.get("thought_signature"):
                call["extra_content"] = {
                    "google": {"thought_signature": state["thought_signature"]}
                }
            # No text part: Gemini rejects empty text ("" content) with HTTP 400.
            assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
            if state.get("reasoning_content"):
                assistant["reasoning_content"] = state["reasoning_content"]
            messages.append(assistant)
            result = {"role": "tool", "tool_call_id": step.call_id, "content": step.output}
            if self.provider == "gemini":
                result["name"] = step.tool  # Gemini answers a function call by name
            messages.append(result)
        return messages

    def function(self, tool) -> dict:
        function = {"name": tool.name, "description": tool.description}
        if self.provider == "gemini":
            parameters = gemini_parameters(tool.parameters)
            if parameters is not None:
                function["parameters"] = parameters
        else:
            function["parameters"] = tool.parameters
        return {"type": "function", "function": function}

    def decide(self, turn: Turn) -> Reply:
        request: dict[str, Any] = {"messages": self.messages(turn)}
        if turn.tools:
            request["tools"] = [self.function(tool) for tool in turn.tools]
        response = self._create(**request)
        message = response.choices[0].message
        usage = getattr(response, "usage", None)
        reply = Reply(text=message.content or "", total_tokens=getattr(usage, "total_tokens", None))
        if message.tool_calls:
            call = message.tool_calls[0]  # NLG runs one action per decision
            reply.tool_call = ToolCall(call.function.name, parse_arguments(call.function.arguments))
            state = {"call_id": call.id}
            google = ((call.model_extra or {}).get("extra_content") or {}).get("google") or {}
            if google.get("thought_signature"):
                state["thought_signature"] = google["thought_signature"]
            reasoning = (message.model_extra or {}).get("reasoning_content")
            if reasoning:
                state["reasoning_content"] = reasoning
            reply.state = state
        return reply

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        response = self._create(
            messages=[
                {"role": "system", "content": system + JSON_NOTE},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return parse_json_object(response.choices[0].message.content or "")
