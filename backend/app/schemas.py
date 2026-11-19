"""Request and response bodies of the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]


class ModelCredentials(BaseModel):
    api_key: str = Field(
        ..., min_length=1, description="The user's own model API key. Never stored."
    )
    model: str | None = Field(default=None, description="Model name; defaults to SW_DEFAULT_MODEL.")


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
    session: SessionView


class KnowledgeView(BaseModel):
    pages: list[dict[str, Any]]
    proposals: list[dict[str, Any]]
