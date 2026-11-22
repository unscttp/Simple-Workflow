"""HTTP API of the Simple Workflow assistant.

Run with ``uvicorn app.main:app`` from ``backend/``. Interactive documentation
is served at ``/docs``.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from workflow_core import render_page

from .agent import AgentService, ModelChoice, ModelFactory, NoPendingTurn, TurnInProgress
from .providers import ModelError, create_model, list_models
from .schemas import (
    ApiKey,
    AutoRetryView,
    DecisionRequest,
    KnowledgeView,
    MessageRequest,
    ModelList,
    ProposalDecision,
    ProvidersView,
    ProviderView,
    RiskLevelRequest,
    SessionView,
    TurnResponse,
)
from .sessions import SessionNotFound
from .settings import Settings
from .tools.research import SearchFunction
from .tools.scholar import ScholarFunction

KNOWLEDGE_REVIEWER = "web-user"


def create_app(
    settings: Settings | None = None,
    model_factory: ModelFactory = create_model,
    search: SearchFunction | None = None,
    scholar: ScholarFunction | None = None,
    model_lister=list_models,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.default_provider not in settings.providers:
        raise ValueError(f"SW_DEFAULT_PROVIDER must be one of {sorted(settings.providers)}.")
    agent = AgentService(settings, model_factory, search=search, scholar=scholar)
    app = FastAPI(title="Simple Workflow assistant", version="2.1.0")
    app.state.agent = agent
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    def session_view(session_id: str) -> SessionView:
        try:
            session = agent.sessions.load(session_id)
        except SessionNotFound:
            raise HTTPException(404, "Session not found.")
        return SessionView(
            id=session.id,
            risk_level=session.risk_level,
            workspace=session.workspace,
            messages=session.messages,
            pending_approvals=agent.pending(session),
        )

    def turn(session_id: str, run) -> TurnResponse:
        try:
            result = run()
        except SessionNotFound:
            raise HTTPException(404, "Session not found.")
        except (TurnInProgress, NoPendingTurn) as exc:
            raise HTTPException(409, str(exc))
        except KeyError:
            raise HTTPException(404, "Approval request not found.")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return TurnResponse(**asdict(result), session=session_view(session_id))

    def choice(request) -> ModelChoice:
        return ModelChoice(
            provider=request.provider or settings.default_provider,
            api_key=request.api_key,
            model=request.model,
            auto_retry=request.auto_retry,
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "search": agent.search_source}

    @app.get("/api/providers", response_model=ProvidersView)
    def providers() -> ProvidersView:
        return ProvidersView(
            default=settings.default_provider,
            auto_retry=AutoRetryView(
                default=settings.model_retries > 0, attempts=settings.model_retries
            ),
            providers=[
                ProviderView(
                    id=p.id, label=p.label, default_model=p.default_model, key_hint=p.key_hint
                )
                for p in settings.providers.values()
            ],
        )

    @app.post("/api/providers/{provider_id}/models", response_model=ModelList)
    def provider_models(provider_id: str, body: ApiKey) -> ModelList:
        provider = settings.providers.get(provider_id)
        if provider is None:
            raise HTTPException(404, "Unknown model provider.")
        try:
            models = model_lister(provider, body.api_key)
        except ModelError as exc:
            raise HTTPException(502, str(exc))
        return ModelList(provider=provider_id, models=models)

    @app.post("/api/sessions", response_model=SessionView, status_code=201)
    def create_session() -> SessionView:
        return session_view(agent.create_session().id)

    @app.get("/api/sessions/{session_id}", response_model=SessionView)
    def get_session(session_id: str) -> SessionView:
        return session_view(session_id)

    @app.put("/api/sessions/{session_id}/risk-level", response_model=SessionView)
    def set_risk_level(session_id: str, body: RiskLevelRequest) -> SessionView:
        try:
            agent.set_risk_level(session_id, body.risk_level)
        except SessionNotFound:
            raise HTTPException(404, "Session not found.")
        return session_view(session_id)

    @app.delete("/api/sessions/{session_id}/workspace", response_model=SessionView)
    def revoke_workspace(session_id: str) -> SessionView:
        try:
            agent.revoke_workspace(session_id)
        except SessionNotFound:
            raise HTTPException(404, "Session not found.")
        return session_view(session_id)

    @app.get("/api/sessions/{session_id}/graph")
    def graph(session_id: str, task_id: str | None = None) -> dict:
        """The turn's workflow graph and where it is; poll it while a turn runs."""
        try:
            return agent.graph(session_id, task_id)
        except SessionNotFound:
            raise HTTPException(404, "Session not found.")

    @app.post("/api/sessions/{session_id}/messages", response_model=TurnResponse)
    def send_message(session_id: str, body: MessageRequest) -> TurnResponse:
        return turn(
            session_id,
            lambda: agent.send_message(session_id, body.message, choice(body)),
        )

    @app.post("/api/sessions/{session_id}/approvals/{request_id}", response_model=TurnResponse)
    def decide(session_id: str, request_id: str, body: DecisionRequest) -> TurnResponse:
        return turn(
            session_id,
            lambda: agent.decide(
                session_id,
                request_id,
                body.approved,
                choice(body),
                body.comment,
            ),
        )

    @app.get("/api/knowledge", response_model=KnowledgeView)
    def knowledge() -> KnowledgeView:
        return KnowledgeView(
            pages=agent.knowledge.index(), proposals=agent.knowledge.proposals("pending")
        )

    @app.get("/api/knowledge/pages/{scope}/{slug}")
    def knowledge_page(scope: str, slug: str) -> dict:
        try:
            page = agent.knowledge.read_page(f"{scope}/{slug}")
        except (KeyError, ValueError):
            raise HTTPException(404, "Knowledge page not found.")
        return {"page": f"{scope}/{slug}", "version": page["version"], "content": render_page(page)}

    @app.post("/api/knowledge/proposals/{proposal_id}")
    def review_proposal(proposal_id: str, body: ProposalDecision) -> dict:
        try:
            return agent.knowledge.resolve_proposal(
                proposal_id, body.approved, reviewer=KNOWLEDGE_REVIEWER, comment=body.comment
            )
        except KeyError:
            raise HTTPException(404, "Proposal not found.")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/knowledge/changes/{change_id}/revert")
    def revert_change(change_id: str) -> dict:
        try:
            return agent.knowledge.revert(change_id, reviewer=KNOWLEDGE_REVIEWER)
        except KeyError:
            raise HTTPException(404, "Change not found.")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    return app


app = create_app()
