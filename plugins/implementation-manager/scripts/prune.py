"""Remove a repo's old run folders: `run.py prune`.

Claude Code never ages out plugin data, so the manager prunes when it starts a
run. A run folder is removed once nothing in it has changed for the retention
period. Its age is taken from the newest file under it, not from `run.json`,
because children append to `requests.log` while `run.json` sits still. Symlinks
are never followed, for age or for removal, and only folders holding `run.json`
count as runs. The run being started is never touched.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

DEFAULT_DAYS = 30
RUN_FILE = "run.json"


def retention_days(value: str) -> int:
    """Parse `--days`: empty or an unexpanded `${user_config...}` means DEFAULT_DAYS.

    The skill passes the plugin's `retention_days` option as
    '${user_config.retention_days}'. Claude Code fills it in only once the user
    saves a value; until then the skill text keeps the literal.
    """
    if value == "" or value.startswith("${"):
        return DEFAULT_DAYS
    try:
        days = int(value)
    except ValueError:
        days = 0
    if days < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of days, at least 1: {value!r}")
    return days


def _newest_mtime(run: Path) -> float:
    """The newest lstat mtime of `run` and everything under it, never following symlinks."""
    newest = run.lstat().st_mtime
    for root, dirs, files in os.walk(run, onerror=_raise):
        for name in dirs + files:
            newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
    return newest


def _raise(exc: OSError) -> None:
    raise exc


def prune_runs(keep: Path, days: int, now: float) -> dict:
    """Remove run folders beside `keep` untouched for over `days` days; never `keep`."""
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []
    repo_dir = keep.parent
    if not repo_dir.is_dir():
        return {"removed": removed, "skipped": skipped, "errors": errors}
    keep_resolved = keep.resolve()
    for entry in sorted(repo_dir.iterdir()):
        if entry.is_symlink():
            skipped.append(str(entry))
            continue
        if not entry.is_dir() or entry.resolve() == keep_resolved:
            continue
        if not (entry / RUN_FILE).is_file():
            skipped.append(str(entry))
            continue
        try:
            if now - _newest_mtime(entry) > days * 86400:
                shutil.rmtree(entry)
                removed.append(str(entry))
        except OSError as exc:
            errors.append({"dir": str(entry), "error": str(exc)})
    return {"removed": removed, "skipped": skipped, "errors": errors}
