"""Shared fixtures for the docs_inventory tests.

Tests import the script as `from helpers import docs_inventory`, never with a
bare `import docs_inventory`: isort puts `docs_inventory` above `helpers`, and
the bare import would run before this module puts the scripts dir on sys.path.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "superpowers-cleanup" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Drop variables that point git at another repository or index. They are set
# when the suite runs from a git hook (for example pre-commit), and would make
# every fixture command act on the outer repository instead.
for _var in (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_GRAFT_FILE",
    "GIT_REPLACE_REF_BASE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
):
    os.environ.pop(_var, None)

# Isolate fixture repos from the developer's global/system git configuration.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

import docs_inventory  # noqa: E402

__all__ = ["docs_inventory"]

SPECS = "docs/superpowers/specs"
PLANS = "docs/superpowers/plans"
RESEARCH = "docs/superpowers/research"


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


def write(repo: Path, rel: str, content: str | bytes) -> Path:
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def commit_all(repo: Path, message: str, date: str = "2020-01-01T12:00:00") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message, env={"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})


def make_repo(tmp: Path, branch: str = "main") -> Path:
    """Create `tmp/repo` with one commit of README.md on `branch`."""
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", branch)
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    write(repo, "README.md", "# Test repo\n")
    commit_all(repo, "initial commit")
    return repo


def call(*argv: str) -> tuple[int, str, str]:
    """Invoke the CLI's main(); return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = docs_inventory.main(list(argv))
        except SystemExit as exc:  # argparse usage errors
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def run(*argv: str) -> dict:
    """Invoke the CLI's main(), assert it succeeded, and return the parsed JSON."""
    code, out, err = call(*argv)
    assert code == 0, f"main exited with {code!r}: {err}"
    return json.loads(out)


def _one(out: dict, pred, label: str) -> dict:
    found = [f for f in out["files"] if pred(f)]
    if len(found) != 1:
        raise AssertionError(f"expected one file matching {label}, found {[f['path'] for f in found]}")
    return found[0]


def by_path(out: dict, path: str) -> dict:
    return _one(out, lambda f: f["path"] == path, path)


def by_suffix(out: dict, suffix: str) -> dict:
    return _one(out, lambda f: f["path"].endswith(suffix), f"*{suffix}")
