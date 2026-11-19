"""The assistant's tools, registered on one NLG tool module."""

from __future__ import annotations

from pathlib import Path

from workflow_core import LocalToolModule

from ..sessions import SessionStore
from .analysis import TOOLS as ANALYSIS_TOOLS
from .reports import ReportTools
from .research import WEB_SEARCH, SearchFunction, duckduckgo_search, web_search_handler

# Tools whose results the host vouches for: tasks that only used these (and the
# framework's neutral tools) may have knowledge proposals applied automatically
# when human review of knowledge is switched off.
TRUSTED_ACTIONS = {"tool:data.analyze_trend"}


def build_tools(
    sessions: SessionStore,
    *,
    reports_root: Path | None = None,
    search: SearchFunction = duckduckgo_search,
) -> LocalToolModule:
    module = LocalToolModule()
    module.register(WEB_SEARCH, web_search_handler(search))
    for definition, handler in ANALYSIS_TOOLS:
        module.register(definition, handler)
    for definition, handler in ReportTools(sessions, reports_root).tools():
        module.register(definition, handler)
    return module
