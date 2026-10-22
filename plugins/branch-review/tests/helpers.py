"""Shared fixtures for the branch-review state script tests."""

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
        code = review_state.main(
            [*argv, "--state-dir", str(state_dir), "--repo", str(repo)]
        )
    assert code in (0, None), "main exited with %r" % (code,)
    return json.loads(buf.getvalue())
