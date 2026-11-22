"""The assistant's tools, registered on one NLG tool module."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from workflow_core import LocalToolModule, Task, register_skill

from ..sessions import SessionStore
from ..skills import install_skill
from .analysis import TOOLS as ANALYSIS_TOOLS
from .reports import ReportTools
from .research import WEB_SEARCH, SearchFunction, web_search_handler
from .scholar import SCHOLAR_SEARCH, ScholarFunction, scholar_search_handler

# Tools whose results the host vouches for: tasks that only used these (and the
# framework's neutral tools) may have knowledge proposals applied automatically
# when human review of knowledge is switched off.
TRUSTED_ACTIONS = {"tool:data.analyze_trend"}

# Agent Skills the assistant can open, with the scripts each may run (after approval).
# They are installed into the data folder (see app.skills) and registered from there.
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
SKILLS = {"survey-creator": ["scripts/check_survey.py"]}


def build_tools(
    sessions: SessionStore,
    *,
    reports_root: Path | None = None,
    search: SearchFunction,
    scholar: ScholarFunction,
    search_source: str = "web",
    session_of: Callable[[Task], str] | None = None,
    skills_dir: Path | None = None,
) -> LocalToolModule:
    module = LocalToolModule()
    module.register(WEB_SEARCH, web_search_handler(search, search_source))
    module.register(SCHOLAR_SEARCH, scholar_search_handler(scholar))
    for definition, handler in ANALYSIS_TOOLS:
        module.register(definition, handler)
    for definition, handler in ReportTools(sessions, reports_root, session_of).tools():
        module.register(definition, handler)
    for name, scripts in SKILLS.items():
        package = SKILLS_DIR / name
        if skills_dir is not None:
            package = install_skill(package, Path(skills_dir) / name)
        register_skill(module, package, scripts=scripts)
    return module
