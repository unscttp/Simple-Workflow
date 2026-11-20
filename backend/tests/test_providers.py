"""Each provider's request format and continuation state, against fake SDK clients.

Responses are built from the SDKs' own types, so ``model_dump`` and
``model_extra`` behave as they do with live replies.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import anthropic
import httpx
import openai
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from openai.types.chat import ChatCompletion
from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem

from app.providers import PROVIDERS, create_model, list_models
from app.providers.anthropic_messages import FALLBACK_BETA, ClaudeModel
from app.providers.base import KNOWLEDGE_SCHEMA, ModelError, Step, ToolSpec, Turn
from app.providers.chat_completions import ChatCompletionsModel
from app.providers.openai_responses import ResponsesModel

TOOLS = [ToolSpec("web_search", "Search.", {"type": "object", "properties": {}})]


def turn(*steps: Step, replay_safe: bool = True) -> Turn:
    return Turn(
        system="SYSTEM",
        conversation=[
            {"role": "user", "content": "before"},
            {"role": "assistant", "content": "ok"},
        ],
        message="now",
        tools=TOOLS,
        steps=list(steps),
        replay_safe=replay_safe,
    )


def step(state: dict | None, call_id: str = "c1") -> Step:
    return Step("web_search", {"query": "q"}, call_id, '{"results": []}', state=state)


class Recorder:
    """Stands in for ``client.<path>.create``: records requests, plays responses."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def completion(message: dict, tokens: int = 30) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": tokens - 10,
                "total_tokens": tokens,
            },
        }
    )


def tool_call_message(**extra) -> dict:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "web_search", "arguments": '{"query": "agents"}'},
    }
    call.update(extra.pop("call_extra", {}))
    return {"role": "assistant", "content": None, "tool_calls": [call], **extra}


def status_error(module, status: int, message: str):
    request = httpx.Request("POST", "https://api.example.com")
    response = httpx.Response(status, request=request)
    return module.APIStatusError(message, response=response, body=None)


class ChatCompletionsTests(unittest.TestCase):
    def model(self, *responses, provider="gemini", temperature=None):
        recorder = Recorder(*responses)
        client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
        return ChatCompletionsModel(provider, "m", client, temperature=temperature), recorder

    def test_gemini_thought_signature_round_trip(self):
        signature = {"extra_content": {"google": {"thought_signature": "SIG"}}}
        model, recorder = self.model(completion(tool_call_message(call_extra=signature)))
        reply = model.decide(turn())
        self.assertEqual(reply.tool_call.name, "web_search")
        self.assertEqual(reply.tool_call.arguments, {"query": "agents"})
        self.assertEqual(reply.state, {"call_id": "call-1", "thought_signature": "SIG"})
        self.assertEqual(reply.total_tokens, 30)
        self.assertNotIn("temperature", recorder.requests[0])

        messages = model.messages(turn(step(reply.state, "call-1")))
        self.assertEqual(
            [m["role"] for m in messages],
            ["system", "user", "assistant", "user", "assistant", "tool"],
        )
        (call,) = messages[-2]["tool_calls"]
        self.assertEqual(call["extra_content"], {"google": {"thought_signature": "SIG"}})
        self.assertEqual(messages[-1]["tool_call_id"], "call-1")

    def test_deepseek_reasoning_content_and_temperature(self):
        reply_message = tool_call_message(reasoning_content="thinking...")
        model, recorder = self.model(
            completion(reply_message), provider="deepseek", temperature=0.2
        )
        reply = model.decide(turn())
        self.assertEqual(reply.state["reasoning_content"], "thinking...")
        self.assertEqual(recorder.requests[0]["temperature"], 0.2)
        assistant = model.messages(turn(step(reply.state, "call-1")))[-2]
        self.assertEqual(assistant["reasoning_content"], "thinking...")
        self.assertNotIn("extra_content", assistant["tool_calls"][0])

    def test_answer_json_and_errors(self):
        model, recorder = self.model(
            completion({"role": "assistant", "content": "final"}),
            completion({"role": "assistant", "content": '```json\n{"operations": []}\n```'}),
            status_error(openai, 401, "Incorrect API key provided: sk-secret"),
        )
        reply = model.decide(turn())
        self.assertEqual((reply.text, reply.tool_call, reply.state), ("final", None, None))
        self.assertEqual(model.complete_json("S", "U", KNOWLEDGE_SCHEMA), {"operations": []})
        self.assertEqual(recorder.requests[1]["response_format"], {"type": "json_object"})
        with self.assertRaises(ModelError) as caught:
            model.decide(turn())
        self.assertEqual(str(caught.exception), "Model request failed with HTTP 401.")
        # Tracebacks leave out the SDK error, whose text can quote the key.
        self.assertTrue(caught.exception.__suppress_context__)


def responses_reply(*items, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        output=list(items), output_text=text, usage=SimpleNamespace(total_tokens=42)
    )


REASONING = ResponseReasoningItem(
    id="rs_1", type="reasoning", summary=[], encrypted_content="ENCRYPTED"
)
FUNCTION_CALL = ResponseFunctionToolCall(
    id="fc_1", type="function_call", call_id="call-1", name="web_search", arguments='{"query": "q"}'
)


class ResponsesTests(unittest.TestCase):
    def model(self, *responses):
        recorder = Recorder(*responses)
        return ResponsesModel("gpt", SimpleNamespace(responses=recorder)), recorder

    def test_reasoning_items_are_replayed(self):
        model, recorder = self.model(responses_reply(REASONING, FUNCTION_CALL))
        reply = model.decide(turn())
        request = recorder.requests[0]
        self.assertEqual(request["instructions"], "SYSTEM")
        self.assertIs(request["store"], False)
        self.assertIs(request["parallel_tool_calls"], False)
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertIs(request["tools"][0]["strict"], False)
        self.assertEqual(reply.state["call_id"], "call-1")
        self.assertEqual(
            [item["type"] for item in reply.state["items"]], ["reasoning", "function_call"]
        )
        self.assertEqual(reply.state["items"][0]["encrypted_content"], "ENCRYPTED")

        items = model.input_items(turn(step(reply.state, "call-1")))
        self.assertEqual(items[:3], [*turn().conversation, {"role": "user", "content": "now"}])
        self.assertEqual(items[3:5], reply.state["items"])
        self.assertEqual(
            items[5],
            {"type": "function_call_output", "call_id": "call-1", "output": '{"results": []}'},
        )

    def test_synthesized_call_without_state_or_when_unsafe(self):
        state = {"call_id": "call-1", "items": [{"type": "reasoning", "id": "rs_1"}]}
        for current in (step(None, "call-1"), step(state, "call-1")):
            safe = current.state is None
            items = ResponsesModel.input_items(turn(current, replay_safe=safe))
            self.assertEqual(
                items[3],
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "web_search",
                    "arguments": '{"query": "q"}',
                },
            )

    def test_answer_and_json(self):
        model, recorder = self.model(
            responses_reply(text="final"), responses_reply(text='{"operations": []}')
        )
        reply = model.decide(turn())
        self.assertEqual((reply.text, reply.tool_call, reply.total_tokens), ("final", None, 42))
        self.assertEqual(model.complete_json("S", "U", KNOWLEDGE_SCHEMA), {"operations": []})
        self.assertEqual(recorder.requests[1]["text"], {"format": {"type": "json_object"}})


def claude_message(content: list[dict], stop_reason: str = "tool_use", beta: bool = True, **extra):
    cls = BetaMessage if beta else Message
    return cls.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 7,
                "cache_creation_input_tokens": 11,
                "cache_read_input_tokens": 13,
            },
            **extra,
        }
    )


THINKING = {"type": "thinking", "thinking": "plan", "signature": "SIG"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {"query": "q"}}


class ClaudeTests(unittest.TestCase):
    def model(self, name, *responses):
        plain, beta = Recorder(), Recorder()
        target = beta if name == "claude-opus-5-5" else plain
        target.responses.extend(responses)
        client = SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta))
        return ClaudeModel(name, client), target

    def test_thinking_blocks_are_replayed_while_safe(self):
        model, recorder = self.model(
            "claude-opus-5-5",
            claude_message([THINKING, {"type": "text", "text": "Searching."}, TOOL_USE]),
        )
        reply = model.decide(turn())
        request = recorder.requests[0]
        self.assertEqual(request["betas"], [FALLBACK_BETA])
        self.assertEqual(request["fallbacks"], "default")
        self.assertEqual(request["cache_control"], {"type": "ephemeral"})
        self.assertEqual(
            request["tool_choice"], {"type": "auto", "disable_parallel_tool_use": True}
        )
        self.assertEqual(request["tools"][0]["input_schema"], TOOLS[0].parameters)
        self.assertEqual(reply.tool_call.arguments, {"query": "q"})
        self.assertEqual(reply.total_tokens, 36)
        self.assertEqual(reply.state["call_id"], "toolu_1")
        self.assertEqual(
            [b["type"] for b in reply.state["content"]], ["thinking", "text", "tool_use"]
        )

        messages = model.messages(turn(step(reply.state, "toolu_1")))
        self.assertEqual(messages[-2], {"role": "assistant", "content": reply.state["content"]})
        self.assertEqual(
            messages[-1],
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"results": []}'}
                ],
            },
        )
        stripped = model.messages(turn(step(reply.state, "toolu_1"), replay_safe=False))[-2]
        self.assertEqual([b["type"] for b in stripped["content"]], ["text", "tool_use"])

    def test_synthesized_tool_use_and_error_results(self):
        failed = Step("web_search", {"query": "q"}, "toolu_9", '{"error": "x"}', is_error=True)
        assistant, result = ClaudeModel.messages(turn(failed))[-2:]
        self.assertEqual(
            assistant["content"],
            [{"type": "tool_use", "id": "toolu_9", "name": "web_search", "input": {"query": "q"}}],
        )
        self.assertIs(result["content"][0]["is_error"], True)

    def test_other_models_use_the_plain_endpoint(self):
        model, recorder = self.model(
            "claude-haiku-4-5",
            claude_message([{"type": "text", "text": "hi"}], "end_turn", beta=False),
        )
        reply = model.decide(turn())
        self.assertEqual((reply.text, reply.tool_call), ("hi", None))
        self.assertNotIn("fallbacks", recorder.requests[0])
        self.assertNotIn("betas", recorder.requests[0])

    def test_refusal_cut_off_and_errors(self):
        model, _ = self.model(
            "claude-opus-5-5",
            claude_message([], "refusal", stop_details={"type": "refusal", "category": "cyber"}),
            claude_message([], "max_tokens"),
            status_error(anthropic, 401, "invalid x-api-key sk-ant-secret"),
        )
        with self.assertRaisesRegex(ModelError, "declined"):
            model.decide(turn())
        with self.assertRaisesRegex(ModelError, "cut off"):
            model.decide(turn())
        with self.assertRaises(ModelError) as caught:
            model.decide(turn())
        self.assertEqual(str(caught.exception), "Model request failed with HTTP 401.")

    def test_json_uses_structured_output(self):
        model, recorder = self.model(
            "claude-opus-5-5",
            claude_message([{"type": "text", "text": '{"operations": []}'}], "end_turn"),
        )
        self.assertEqual(model.complete_json("S", "U", KNOWLEDGE_SCHEMA), {"operations": []})
        self.assertEqual(
            recorder.requests[0]["output_config"],
            {"format": {"type": "json_schema", "schema": KNOWLEDGE_SCHEMA}},
        )


class FactoryTests(unittest.TestCase):
    def test_each_provider_gets_its_api(self):
        kinds = {pid: type(create_model(p, " key ", "m")) for pid, p in PROVIDERS.items()}
        self.assertEqual(
            kinds,
            {
                "deepseek": ChatCompletionsModel,
                "gemini": ChatCompletionsModel,
                "openai": ResponsesModel,
                "anthropic": ClaudeModel,
            },
        )
        with self.assertRaises(ValueError):
            create_model(PROVIDERS["openai"], "  ", "m")
        with self.assertRaises(ValueError):
            list_models(PROVIDERS["openai"], "")


if __name__ == "__main__":
    unittest.main()
