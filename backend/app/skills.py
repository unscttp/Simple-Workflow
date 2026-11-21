"""Install the bundled Agent Skills into the data folder before registering them.

Skills are registered from ``<data dir>/skills/<name>`` rather than from the
application folder, because NLG writes graphs into the skill package when
the user agrees to save one (``graphs/<id>.json`` and ``.md``). Kept in the
data folder, those saved graphs survive upgrades and container rebuilds.

At every start the bundled files are copied over the installed copy, so the
installed skill always matches this version of the application. Files the
application no longer ships are removed; files it never shipped (saved
graphs) are kept.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

BUNDLED_LIST = ".bundled.json"  # files copied from the application last time


def _files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def install_skill(source: Path, target: Path) -> Path:
    """Copy one bundled skill package over its installed copy; return the copy."""
    target.mkdir(parents=True, exist_ok=True)
    shipped = _files(source)
    try:
        previous = json.loads((target / BUNDLED_LIST).read_text("utf-8"))
    except (OSError, ValueError):
        previous = []
    for relative in set(previous) - set(shipped):
        stale = target / relative
        if stale.is_file() and not stale.is_symlink():
            stale.unlink()
    for relative in shipped:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    (target / BUNDLED_LIST).write_text(json.dumps(shipped, indent=2), "utf-8")
    return target
