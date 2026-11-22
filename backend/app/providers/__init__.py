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
from .base import ChatModel, ModelError, provider_message
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

TIMEOUT_SECONDS = 120


def _openai_client(api_key: str, base_url: str | None):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url, timeout=TIMEOUT_SECONDS)


def _anthropic_client(api_key: str, base_url: str | None):
    from anthropic import Anthropic

    return Anthropic(api_key=api_key, base_url=base_url, timeout=TIMEOUT_SECONDS)


def _key(api_key: str) -> str:
    if not api_key or not api_key.strip():
        raise ValueError("An API key is required.")
    return api_key.strip()


def create_model(provider: Provider, api_key: str, model: str) -> ChatModel:
    key = _key(api_key)
    if provider.id == "anthropic":
        return ClaudeModel(model, _anthropic_client(key, provider.base_url))
    if provider.id == "openai":
        return ResponsesModel(model, _openai_client(key, provider.base_url))
    # Gemini recommends its default temperature; DeepSeek keeps the low value used so far.
    temperature = 0.2 if provider.id == "deepseek" else None
    return ChatCompletionsModel(
        provider.id, model, _openai_client(key, provider.base_url), temperature=temperature
    )


def list_models(provider: Provider, api_key: str) -> list[str]:
    """Model IDs the key can use, as reported by the provider."""
    key = _key(api_key)
    if provider.id == "anthropic":
        import anthropic

        client = _anthropic_client(key, provider.base_url)
        try:
            ids = [model.id for model in client.models.list(limit=100)]
        except anthropic.APIStatusError as exc:
            reason = provider_message(exc.body, (key,))
            detail = f": {reason}" if reason else "."
            raise ModelError(f"Listing models failed with HTTP {exc.status_code}{detail}") from None
        except anthropic.APIError as exc:
            raise ModelError(f"Listing models failed: {type(exc).__name__}.") from None
    else:
        client = _openai_client(key, provider.base_url)
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
    "Provider",
    "create_model",
    "list_models",
]
