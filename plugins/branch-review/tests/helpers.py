"""Shared fixtures for the branch-review state script tests."""

from collections.abc import Iterator
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
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

import review_state  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo: Path, name: str, content: str, message: str) -> None:
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def make_repo(
    tmp: Path,
    *,
    origin: Optional[str] = "git@github.com:Owner/Repo.git",
    branch: str = "feature/x",
) -> Path:
    """Create a repo with one commit on main and a second commit on `branch`.

    The repo lives in `tmp/repo`, so use a distinct `tmp` per repo.
    """
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    commit_file(repo, "a.txt", "a\n", "initial commit")
    if origin is not None:
        git(repo, "remote", "add", "origin", origin)
    git(repo, "checkout", "-b", branch)
    commit_file(repo, "b.txt", "b\n", "add b")
    return repo


def run(repo: Path, *argv: str, state_dir: Path) -> dict:
    """Invoke the CLI's main() against `repo` and return the parsed JSON output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = review_state.main([*argv, "--state-dir", str(state_dir), "--repo", str(repo)])
    assert code in (0, None), f"main exited with {code!r}"
    return json.loads(buf.getvalue())


CANARY = Path("canary") / "a" / "b" / "keep.txt"


@contextlib.contextmanager
def sandbox_cwd() -> Iterator[Path]:
    """Run inside a fresh temp directory holding a canary tree; check it survives.

    Every test that exercises `cleanup` or `prune`, or passes an empty, blank or
    relative `--state-dir`, runs inside this, never with the checkout as cwd.
    """
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="br-sandbox-") as tmp:
        sandbox = Path(tmp).resolve()
        canary = sandbox / CANARY
        canary.parent.mkdir(parents=True)
        canary.write_text("canary\n")
        os.chdir(str(sandbox))
        try:
            yield sandbox
        finally:
            os.chdir(previous)
        if not canary.is_file():
            raise AssertionError(f"canary {canary} was removed")
