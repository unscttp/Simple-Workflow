"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .providers import PROVIDERS, Provider

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BACKEND_DIR / "config"
PROMPT_FILE = BACKEND_DIR / "prompts" / "system_prompt.md"


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _providers_from_env() -> dict[str, Provider]:
    providers = {}
    for provider_id, provider in PROVIDERS.items():
        prefix = f"SW_{provider_id.upper()}_"
        model = os.environ.get(prefix + "MODEL")
        base_url = os.environ.get(prefix + "BASE_URL")
        if provider_id == "deepseek":
            # Names used before several providers were supported.
            model = model or os.environ.get("SW_DEFAULT_MODEL")
            base_url = base_url or os.environ.get("SW_MODEL_BASE_URL")
        providers[provider_id] = replace(
            provider,
            default_model=model or provider.default_model,
            base_url=base_url or provider.base_url,
        )
    return providers


@dataclass(frozen=True)
class Settings:
    # Sessions, saved tasks and the knowledge base live here.
    data_dir: Path = BACKEND_DIR.parent / "data"
    # When set, report folders can only be granted inside this directory.
    reports_root: Path | None = None
    # Model providers with any SW_<PROVIDER>_MODEL / SW_<PROVIDER>_BASE_URL overrides.
    providers: dict[str, Provider] = field(default_factory=lambda: dict(PROVIDERS))
    default_provider: str = "deepseek"
    # Ask the model what each finished turn should add to long-term knowledge.
    knowledge_consolidation: bool = True
    # Earlier chat messages passed to the model with each turn.
    history_messages: int = 20
    default_risk_level: str = "medium"
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])

    @classmethod
    def from_env(cls) -> Settings:
        reports_root = os.environ.get("SW_REPORTS_ROOT")
        return cls(
            data_dir=Path(os.environ.get("SW_DATA_DIR", str(cls.data_dir))),
            reports_root=Path(reports_root) if reports_root else None,
            providers=_providers_from_env(),
            default_provider=os.environ.get("SW_DEFAULT_PROVIDER", cls.default_provider),
            knowledge_consolidation=_flag("SW_KNOWLEDGE_CONSOLIDATION", True),
            history_messages=int(os.environ.get("SW_HISTORY_MESSAGES", cls.history_messages)),
            default_risk_level=os.environ.get("SW_DEFAULT_RISK_LEVEL", cls.default_risk_level),
            cors_origins=_list("SW_CORS_ORIGINS", ["http://localhost:3000"]),
        )
