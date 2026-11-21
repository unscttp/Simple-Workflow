"""Report files in the folder the user approved for the session.

The folder is never chosen by the model alone. ``workspace.request_access``
needs the user's approval in the UI; only then is the folder stored on the
session, and every report tool reads it from there at call time. The user can
revoke it at any moment through the API.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from workflow_core import Task, ToolDefinition, ToolSafetyPolicy

from ..sessions import SessionStore
from .documents import EDIT_MODES, READABLE_FORMATS, WRITERS, edit_docx, edit_markdown, read_text

REPORT_FORMATS = (".md", ".docx", ".pdf")
MAX_READ_CHARS = 200_000
# \w matches Unicode letters and digits, so Chinese file names are accepted.
_FILENAME = re.compile(r"\w[\w. -]{0,120}")


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_FILE = {"type": "string", "minLength": 1, "maxLength": 128}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 200000}

REQUEST_ACCESS = ToolDefinition(
    "workspace.request_access",
    "low",
    human_check=True,
    description=(
        "Ask the user to approve a folder for saving and editing reports in this session. "
        "The call waits until the user approves or rejects it in the interface."
    ),
    input_schema=_schema(
        {
            "folder": {"type": "string", "minLength": 1, "maxLength": 500},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        ["folder", "purpose"],
    ),
)
LIST = ToolDefinition(
    "reports.list",
    "low",
    description="List the report files (.md, .docx, .pdf) in the approved folder.",
    input_schema=_schema({}, []),
)
READ = ToolDefinition(
    "reports.read",
    "low",
    description="Read the text of a .md or .docx report in the approved folder.",
    input_schema=_schema({"filename": _FILE}, ["filename"]),
)
SAVE = ToolDefinition(
    "reports.save",
    "medium",
    description=(
        "Save a new report as md, docx or pdf in the approved folder. Never overwrites: "
        "use reports.edit to change an existing report. Without filename, one is made "
        "from the date and title."
    ),
    input_schema=_schema(
        {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "content": _TEXT,
            "format": {"enum": ["md", "docx", "pdf"]},
            "filename": _FILE,
        },
        ["title", "content", "format"],
    ),
)
EDIT = ToolDefinition(
    "reports.edit",
    "medium",
    description=(
        "Edit a .md or .docx report in the approved folder: append text, rewrite it "
        "completely, or replace the body of one section (give the section heading). "
        "A timestamped backup is kept next to the file."
    ),
    input_schema=_schema(
        {
            "filename": _FILE,
            "mode": {"enum": list(EDIT_MODES)},
            "content": _TEXT,
            "section": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        ["filename", "mode", "content"],
    ),
)
DELETE = ToolDefinition(
    "reports.delete",
    "high",
    human_check=True,
    description="Delete one report file in the approved folder. Waits for the user's approval.",
    input_schema=_schema({"filename": _FILE}, ["filename"]),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stem(title: str) -> str:
    stem = re.sub(r"[^\w-]+", "-", title).strip("-_")
    return stem[:80] or "report"


class ReportTools:
    """Handlers bound to the session store.

    ``session_of(task)`` names the session a task belongs to. Turns carry it in
    their metadata; tasks of generated graphs are resolved through their parent.
    """

    def __init__(
        self,
        sessions: SessionStore,
        reports_root: Path | None = None,
        session_of: Callable[[Task], str] | None = None,
    ) -> None:
        self.sessions = sessions
        self.reports_root = reports_root.resolve() if reports_root else None
        self.safety = ToolSafetyPolicy()
        self.session_of = session_of or (lambda task: task.metadata["session_id"])

    # -- helpers -------------------------------------------------------------

    def _session(self, context):
        return self.sessions.load(self.session_of(context.task))

    def _workspace(self, context) -> Path:
        folder = self._session(context).workspace
        if not folder:
            raise PermissionError(
                "No report folder is approved for this session; call "
                "workspace.request_access first."
            )
        path = Path(folder)
        if not path.is_dir():
            raise FileNotFoundError("The approved report folder no longer exists.")
        return path

    def _file(self, context, filename: str, *, must_exist: bool = True) -> Path:
        name = filename.strip()
        if not _FILENAME.fullmatch(name) or name in {".", ".."} or Path(name).name != name:
            raise PermissionError("Use a plain file name without folders.")
        if Path(name).suffix.lower() not in REPORT_FORMATS:
            raise ValueError(f"Report files must end with one of {', '.join(REPORT_FORMATS)}.")
        path = self._workspace(context) / name
        if must_exist and not path.is_file():
            raise FileNotFoundError(f"No report named {name!r} in the approved folder.")
        return path

    def check_folder(self, folder: str) -> Path:
        """Validate a folder before it is granted; returns the resolved path."""
        path = Path(folder).expanduser()
        if not path.is_absolute():
            raise ValueError("The folder must be an absolute path.")
        path = path.resolve()
        if any(path.is_relative_to(root) for root in self.safety.protected_paths):
            raise PermissionError("System folders cannot be used for reports.")
        if self.reports_root is not None and not path.is_relative_to(self.reports_root):
            raise PermissionError(f"Report folders must be inside {self.reports_root}.")
        return path

    # -- handlers ------------------------------------------------------------

    def request_access(self, context, tool, args) -> dict:
        # Runs only after the user approved this exact folder and purpose.
        path = self.check_folder(args["folder"])
        path.mkdir(parents=True, exist_ok=True)
        session = self._session(context)
        session.workspace = str(path)
        self.sessions.save(session)
        context.record_activity(
            "workspace.granted", {"folder": str(path), "purpose": args["purpose"]}
        )
        return {"status": "granted", "folder": str(path)}

    def list_reports(self, context, tool, args) -> dict:
        folder = self._workspace(context)
        files = [
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
            for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.lower() in REPORT_FORMATS
        ]
        return {"folder": str(folder), "files": files}

    def read(self, context, tool, args) -> dict:
        path = self._file(context, args["filename"])
        if path.suffix.lower() not in READABLE_FORMATS:
            raise ValueError(f"Only {', '.join(READABLE_FORMATS)} reports can be read.")
        text = read_text(path)
        truncated = len(text) > MAX_READ_CHARS
        context.record_activity("report.read", {"filename": path.name, "sha256": _sha256(path)})
        return {"filename": path.name, "content": text[:MAX_READ_CHARS], "truncated": truncated}

    def save(self, context, tool, args) -> dict:
        extension = "." + args["format"]
        if args.get("filename"):
            name = Path(args["filename"].strip()).stem + extension
        else:
            name = f"{datetime.now():%Y-%m-%d}-{_stem(args['title'])}{extension}"
        path = self._file(context, name, must_exist=False)
        if path.exists():
            raise FileExistsError(f"{name!r} already exists; use reports.edit or another name.")
        WRITERS[args["format"]](path, args["title"], args["content"])
        context.record_activity("report.saved", {"filename": name, "sha256": _sha256(path)})
        return {"status": "saved", "filename": name, "folder": str(path.parent)}

    def edit(self, context, tool, args) -> dict:
        path = self._file(context, args["filename"])
        if path.suffix.lower() not in READABLE_FORMATS:
            raise ValueError(f"Only {', '.join(READABLE_FORMATS)} reports can be edited.")
        mode, section = args["mode"], args.get("section")
        if mode == "replace_section" and not section:
            raise ValueError("replace_section needs the section heading.")
        before = _sha256(path)
        backup = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(path, backup)
        if path.suffix.lower() == ".md":
            original = path.read_text(encoding="utf-8")
            path.write_text(
                edit_markdown(original, mode, args["content"], section), encoding="utf-8"
            )
        else:
            edit_docx(path, mode, args["content"], section)
        details = {"filename": path.name, "mode": mode, "section": section, "backup": backup.name}
        context.record_activity(
            "report.edited", {**details, "sha256_before": before, "sha256_after": _sha256(path)}
        )
        return {"status": "edited", **details}

    def delete(self, context, tool, args) -> dict:
        # Runs only after the user approved deleting this file.
        path = self._file(context, args["filename"])
        digest = _sha256(path)
        path.unlink()
        context.record_activity("report.deleted", {"filename": path.name, "sha256": digest})
        return {"status": "deleted", "filename": path.name}

    def tools(self) -> list:
        return [
            (REQUEST_ACCESS, self.request_access),
            (LIST, self.list_reports),
            (READ, self.read),
            (SAVE, self.save),
            (EDIT, self.edit),
            (DELETE, self.delete),
        ]
