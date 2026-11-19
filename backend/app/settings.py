"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True)
class Settings:
    # Sessions, saved tasks and the knowledge base live here.
    data_dir: Path = BACKEND_DIR.parent / "data"
    # When set, report folders can only be granted inside this directory.
    reports_root: Path | None = None
    model_base_url: str = "https://api.deepseek.com"
    default_model: str = "deepseek-chat"
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
            model_base_url=os.environ.get("SW_MODEL_BASE_URL", cls.model_base_url),
            default_model=os.environ.get("SW_DEFAULT_MODEL", cls.default_model),
            knowledge_consolidation=_flag("SW_KNOWLEDGE_CONSOLIDATION", True),
            history_messages=int(os.environ.get("SW_HISTORY_MESSAGES", cls.history_messages)),
            default_risk_level=os.environ.get("SW_DEFAULT_RISK_LEVEL", cls.default_risk_level),
            cors_origins=_list("SW_CORS_ORIGINS", ["http://localhost:3000"]),
        )
