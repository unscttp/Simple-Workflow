"""Request and response bodies of the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
ProviderId = Literal["deepseek", "anthropic", "openai", "gemini"]


class ApiKey(BaseModel):
    api_key: str = Field(
        ..., min_length=1, description="The user's own API key for the provider. Never stored."
    )


class ModelCredentials(ApiKey):
    provider: ProviderId | None = Field(
        default=None, description="Model provider; defaults to SW_DEFAULT_PROVIDER."
    )
    model: str | None = Field(
        default=None, description="Model ID; defaults to the provider's default model."
    )
    auto_retry: bool | None = Field(
        default=None,
        description="Retry model requests that time out or fail to connect; "
        "defaults to on unless SW_MODEL_RETRIES is 0.",
    )


class MessageRequest(ModelCredentials):
    message: str = Field(..., min_length=1, max_length=20000)


class DecisionRequest(ModelCredentials):
    approved: bool
    comment: str = Field(default="", max_length=1000)


class RiskLevelRequest(BaseModel):
    risk_level: RiskLevel


class ProposalDecision(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    task_id: str | None = None
    at: str | None = None
    model: str | None = None  # "provider/model" that wrote an assistant message


class SessionView(BaseModel):
    id: str
    risk_level: RiskLevel
    workspace: str | None
    messages: list[ChatMessage]
    pending_approvals: list[dict[str, Any]]


class TurnResponse(BaseModel):
    status: Literal["completed", "waiting", "failed"]
    task_id: str
    answer: str | None
    error: str | None
    tool_logs: list[dict[str, Any]]
    audit_entries: list[dict[str, Any]]
    pending_approvals: list[dict[str, Any]]
    stats: dict[str, Any]
    session: SessionView


class KnowledgeView(BaseModel):
    pages: list[dict[str, Any]]
    proposals: list[dict[str, Any]]


class ProviderView(BaseModel):
    id: ProviderId
    label: str
    default_model: str
    key_hint: str


class AutoRetryView(BaseModel):
    default: bool
    attempts: int


class SearchView(BaseModel):
    web: str  # the web search backend in use
    scholar_key: bool  # whether OpenAlex has an API key (otherwise a shared free budget)


class ProvidersView(BaseModel):
    default: ProviderId
    providers: list[ProviderView]
    auto_retry: AutoRetryView
    search: SearchView


class ModelList(BaseModel):
    provider: ProviderId
    models: list[str]
