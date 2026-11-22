"""Model providers the assistant can run on, selected per request.

| id          | API                                         | SDK        |
| ----------- | ------------------------------------------- | ---------- |
| `deepseek`  | Chat Completions                            | `openai`   |
| `openai`    | Responses                                   | `openai`   |
| `anthropic` | Messages                                    | `anthropic`|
| `gemini`    | Chat Completions (Google's OpenAI-compatible endpoint) | `openai` |

Default models and base URLs can be overridden with ``SW_<PROVIDER>_MODEL`` and
``SW_<PROVIDER>_BASE_URL`` (see ``app.settings``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .anthropic_messages import ClaudeModel
from .base import ChatModel, ModelError, TransientModelError, provider_message, request_failed
from .chat_completions import ChatCompletionsModel, wrap_openai_errors
from .openai_responses import ResponsesModel

MODEL_LIST_LIMIT = 200
# OpenAI lists every model of the account; keep the ones that can chat.
_OPENAI_SKIP = (
    "embedding",
    "tts",
    "whisper",
    "dall-e",
    "image",
    "audio",
    "realtime",
    "moderation",
    "transcribe",
    "search",
    "computer-use",
)


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    default_model: str
    base_url: str | None
    key_hint: str
    # Seconds to wait for an answer (SW_MODEL_TIMEOUT_SECONDS).
    timeout_seconds: float = 300


PROVIDERS: dict[str, Provider] = {
    provider.id: provider
    for provider in (
        Provider("deepseek", "DeepSeek", "deepseek-chat", "https://api.deepseek.com", "sk-..."),
        Provider("anthropic", "Claude (Anthropic)", "claude-opus-5-5", None, "sk-ant-..."),
        Provider("openai", "GPT (OpenAI)", "gpt-5.5", "https://api.openai.com/v1", "sk-..."),
        Provider(
            "gemini",
            "Gemini (Google)",
            "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "AIza...",
        ),
    )
}

# Waiting for an answer: long replies (a whole report) can take minutes. Connecting
# should not. The SDKs' own retries are off: NLG retries transient failures when
# automatic retries are on, and journals each attempt.
TIMEOUT_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 15
DEEPSEEK_MAX_TOKENS = 8192


def _timeout(seconds: float):
    import httpx

    return httpx.Timeout(seconds, connect=CONNECT_TIMEOUT_SECONDS)


def _openai_client(api_key: str, base_url: str | None, timeout: float = TIMEOUT_SECONDS):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url, timeout=_timeout(timeout), max_retries=0)


def _anthropic_client(api_key: str, base_url: str | None, timeout: float = TIMEOUT_SECONDS):
    from anthropic import Anthropic

    return Anthropic(api_key=api_key, base_url=base_url, timeout=_timeout(timeout), max_retries=0)


def _key(api_key: str) -> str:
    if not api_key or not api_key.strip():
        raise ValueError("An API key is required.")
    return api_key.strip()


def create_model(provider: Provider, api_key: str, model: str) -> ChatModel:
    key = _key(api_key)
    if provider.id == "anthropic":
        return ClaudeModel(
            model, _anthropic_client(key, provider.base_url, provider.timeout_seconds)
        )
    if provider.id == "openai":
        return ResponsesModel(
            model, _openai_client(key, provider.base_url, provider.timeout_seconds)
        )
    # Gemini recommends its default temperature; DeepSeek keeps the low value used so far.
    # DeepSeek's chat model answers with at most 4096 tokens unless asked for more (up
    # to 8192), too little for a report written in one tool call.
    deepseek = provider.id == "deepseek"
    return ChatCompletionsModel(
        provider.id,
        model,
        _openai_client(key, provider.base_url, provider.timeout_seconds),
        temperature=0.2 if deepseek else None,
        max_tokens=DEEPSEEK_MAX_TOKENS if deepseek else None,
    )


def list_models(provider: Provider, api_key: str) -> list[str]:
    """Model IDs the key can use, as reported by the provider."""
    key = _key(api_key)
    if provider.id == "anthropic":
        import anthropic

        client = _anthropic_client(key, provider.base_url, provider.timeout_seconds)
        try:
            ids = [model.id for model in client.models.list(limit=100)]
        except anthropic.APIStatusError as exc:
            raise request_failed(
                exc.status_code, provider_message(exc.body, (key,)), "Listing models"
            ) from None
        except anthropic.APIError as exc:
            raise ModelError(f"Listing models failed: {type(exc).__name__}.") from None
    else:
        client = _openai_client(key, provider.base_url, provider.timeout_seconds)
        ids = [
            model.id
            for model in wrap_openai_errors(
                lambda: list(client.models.list()), secrets=(key,), what="Listing models"
            )
        ]
        ids = [model_id.removeprefix("models/") for model_id in ids]
        if provider.id == "openai":
            ids = [
                model_id
                for model_id in ids
                if model_id.startswith(("gpt-", "o"))
                and not any(s in model_id for s in _OPENAI_SKIP)
            ]
    return sorted(set(ids))[:MODEL_LIST_LIMIT]


__all__ = [
    "PROVIDERS",
    "ChatModel",
    "ModelError",
    "TransientModelError",
    "Provider",
    "create_model",
    "list_models",
]
