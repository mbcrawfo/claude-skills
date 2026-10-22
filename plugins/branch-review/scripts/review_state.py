#!/usr/bin/env python3
"""State mechanics for the branch-review plugin.

Usage: review_state.py <command> [--state-dir PATH] [--repo PATH] ...

Every command prints one JSON object to stdout and exits 0. Usage and state
errors exit 1 with a one-line message on stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_STATE_DIR = "~/.claude/plugins/data/branch-review"
STATE_FILE = "review.json"
OPEN_STATUSES = ("open", "fixed", "still-open")


class StateError(Exception):
    """A usage or state problem that should be reported as a one-line error."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors follow the one-line, exit 1 contract."""

    def error(self, message):
        raise StateError(message)


# --- git helpers -----------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_try(repo: Path, *args: str) -> Optional[str]:
    """Run git, returning None instead of raising when it exits non-zero."""
    try:
        return _git(repo, *args)
    except subprocess.CalledProcessError:
        return None


# --- keys ------------------------------------------------------------------


def _owner_repo_from_url(url: str) -> Optional[str]:
    """Extract `owner/repo` from an ssh or https remote URL, lowercased."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    if "://" in url:
        path = url.split("://", 1)[1].partition("/")[2]
    elif ":" in url:  # scp-like: git@github.com:Owner/Repo
        path = url.split(":", 1)[1]
    else:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return "%s/%s" % (parts[-2].lower(), parts[-1].lower())


def repo_key(repo: Path) -> str:
    """`owner__repo` from the origin URL, else `local-<hash of git common dir>`."""
    url = _git_try(repo, "config", "--get", "remote.origin.url")
    if url:
        owner_repo = _owner_repo_from_url(url)
        if owner_repo:
            return re.sub(r"[^a-z0-9._/-]", "_", owner_repo).replace("/", "__")
    common = _git(repo, "rev-parse", "--git-common-dir")
    common_dir = (Path(repo) / common).resolve()
    digest = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()
    return "local-" + digest[:12]


def branch_key(repo: Path) -> Tuple[str, str]:
    """`(branch_name, key)`; detached HEAD gives `("HEAD", "detached-<sha7>")`."""
    name = _git_try(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if name:
        return name, name.replace("/", "__")
    sha = _git(repo, "rev-parse", "--short=7", "HEAD")
    return "HEAD", "detached-" + sha


def default_base(repo: Path) -> str:
    """The `origin/HEAD` target (e.g. `origin/main`) if set, else `main`."""
    target = _git_try(repo, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    return target or "main"


# --- state files -----------------------------------------------------------


def state_path(state_dir: Path, repo: Path) -> Path:
    _, bkey = branch_key(repo)
    return Path(state_dir) / repo_key(repo) / bkey


def load_state(dir: Path) -> Optional[dict]:
    path = Path(dir) / STATE_FILE
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise StateError("corrupt state file %s: %s" % (path, exc))
    if not isinstance(state, dict):
        raise StateError("corrupt state file %s: expected a JSON object" % path)
    return state


def save_state(dir: Path, state: dict) -> None:
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / STATE_FILE
    tmp = dir / (STATE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(str(tmp), str(path))


# --- commands --------------------------------------------------------------


def cmd_locate(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    state_dir = Path(args.state_dir).expanduser()
    branch, _ = branch_key(repo)
    directory = state_path(state_dir, repo)
    state = load_state(directory)

    open_findings: List[dict] = []
    rounds = 0
    if state is not None:
        rounds = len(state.get("rounds", []))
        open_findings = [
            f for f in state.get("findings", []) if f.get("status") in OPEN_STATUSES
        ]

    return {
        "repo_key": repo_key(repo),
        "branch": branch,
        "base": args.base or default_base(repo),
        "dir": str(directory),
        "exists": state is not None,
        # Placeholder: follow-up detection replaces "state-exists" later.
        "mode": "deep",
        "reason": "no-state" if state is None else "state-exists",
        "rounds": rounds,
        "open_findings": open_findings,
    }


# --- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument(
        "--state-dir",
        default=DEFAULT_STATE_DIR,
        help="root directory for review state (default: %(default)s)",
    )
    common.add_argument(
        "--repo", default=".", help="repository path (default: current directory)"
    )

    parser = _Parser(
        prog="review_state.py", description="branch-review state helper"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    locate = sub.add_parser(
        "locate", parents=[common], help="find state for the current branch"
    )
    locate.add_argument("--base", help="base ref (overrides the detected default)")
    locate.set_defaults(func=cmd_locate)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        result = args.func(args)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        message = detail[-1] if detail else "git %s failed" % " ".join(exc.cmd[1:3])
        print("error: %s" % message, file=sys.stderr)
        return 1
    except (StateError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
