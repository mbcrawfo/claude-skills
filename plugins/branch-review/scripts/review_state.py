#!/usr/bin/env python3
"""State mechanics for the branch-review plugin.

Usage: review_state.py <command> [--state-dir PATH] [--repo PATH] ...

Every command prints one JSON object to stdout and exits 0. Usage and state
errors exit 1 with a one-line message on stderr.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_STATE_DIR = "~/.claude/plugins/data/branch-review"
STATE_FILE = "review.json"
OPEN_STATUSES = ("open", "fixed", "still-open")
MAX_FILES = 150
MAX_LINES = 10000


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_state(dir: Path, state: dict) -> None:
    dir = Path(dir)
    state["updated"] = _now()
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / STATE_FILE
    tmp = dir / (STATE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(str(tmp), str(path))


# --- snapshots -------------------------------------------------------------


def make_snapshot(repo: Path) -> Tuple[str, bool]:
    """Return `(sha, dirty)` for a commit holding the working tree's content.

    Uses a temporary index so the real index, working tree and refs are never
    touched. A clean tree returns HEAD itself and creates nothing.
    """
    head = _git(repo, "rev-parse", "HEAD")
    if not _git(repo, "status", "--porcelain"):
        return head, False
    with tempfile.TemporaryDirectory(prefix="branch-review-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        # Fallback identity so a machine without git user config still works.
        for var, value in (
            ("GIT_AUTHOR_NAME", "branch-review"),
            ("GIT_AUTHOR_EMAIL", "branch-review@localhost"),
            ("GIT_COMMITTER_NAME", "branch-review"),
            ("GIT_COMMITTER_EMAIL", "branch-review@localhost"),
        ):
            env.setdefault(var, value)

        def run_git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()

        run_git("read-tree", "HEAD")
        run_git("add", "-A")
        tree = run_git("write-tree")
        sha = run_git(
            "commit-tree",
            "--no-gpg-sign",
            tree,
            "-p",
            head,
            "-m",
            "branch-review snapshot",
        )
    return sha, True


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git_try(repo, "merge-base", "--is-ancestor", ancestor, descendant) is not None


def _object_exists(repo: Path, sha: str) -> bool:
    return _git_try(repo, "cat-file", "-e", sha + "^{commit}") is not None


def choose_mode(repo: Path, state: Optional[dict]) -> Tuple[str, str]:
    """`(mode, reason)` for the next round on this branch."""
    if state is None:
        return "deep", "no-state"
    rounds = state.get("rounds", [])
    if not rounds:
        return "deep", "no-rounds"
    last = rounds[-1]
    if not _object_exists(repo, str(last.get("snapshot", ""))):
        return "deep", "snapshot-missing"
    if not _is_ancestor(repo, str(last.get("head", "")), "HEAD"):
        return "deep", "history-rewritten"
    return "followup", "continuing"


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
    mode, reason = choose_mode(repo, state)

    return {
        "repo_key": repo_key(repo),
        "branch": branch,
        "base": args.base or default_base(repo),
        "dir": str(directory),
        "exists": state is not None,
        "mode": mode,
        "reason": reason,
        "rounds": rounds,
        "open_findings": open_findings,
    }


def cmd_snapshot(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    sha, dirty = make_snapshot(repo)
    return {"snapshot": sha, "head": _git(repo, "rev-parse", "HEAD"), "dirty": dirty}


def _numstat(repo: Path, diff_from: str, diff_to: str) -> List[Tuple[str, int]]:
    """`(path, insertions + deletions)` per changed file; binary files count 0."""
    out = _git(repo, "diff", "--numstat", "-z", "--no-renames", diff_from, diff_to)
    entries = []
    for record in out.split("\0"):
        if not record:
            continue
        ins, dels, path = record.split("\t", 2)
        count = (int(ins) if ins.isdigit() else 0) + (int(dels) if dels.isdigit() else 0)
        entries.append((path, count))
    return entries


def cmd_begin_round(args: argparse.Namespace) -> dict:
    repo = Path(args.repo)
    state_dir = Path(args.state_dir).expanduser()
    branch, _ = branch_key(repo)
    directory = state_path(state_dir, repo)
    state = load_state(directory)

    if state is None:
        now = _now()
        state = {
            "repo_key": repo_key(repo),
            "branch": branch,
            "base_ref": args.base_ref,
            "intent": None,
            "created": now,
            "updated": now,
            "rounds": [],
            "findings": [],
        }
    rounds = state.setdefault("rounds", [])
    state.setdefault("findings", [])

    if args.mode == "followup":
        if not rounds:
            raise StateError("cannot start a follow-up round: no previous round")
        diff_from = rounds[-1]["snapshot"]
    else:
        diff_from = _git(repo, "merge-base", "HEAD", args.base_ref)
    diff_to = args.snapshot

    state["base_ref"] = args.base_ref
    if args.intent_file:
        state["intent"] = Path(args.intent_file).read_text(encoding="utf-8")

    entries = _numstat(repo, diff_from, diff_to)
    if args.paths:
        entries = [
            (f, n)
            for f, n in entries
            if any(fnmatch.fnmatchcase(f, pat) for pat in args.paths)
        ]
    changed = [f for f, _ in entries]
    lines = sum(n for _, n in entries)
    files = len(changed)

    rnd = {
        "number": len(rounds) + 1,
        "mode": args.mode,
        "model": args.model,
        "started": _now(),
        "snapshot": diff_to,
        "head": _git(repo, "rev-parse", "HEAD"),
        "base_ref": args.base_ref,
        "diff_from": diff_from,
        "diff_to": diff_to,
        "changed_files": changed,
        "paths": args.paths or [],
        "summary": None,
        "merge_risk": None,
    }
    rounds.append(rnd)
    save_state(directory, state)

    result = dict(rnd)
    result.update(
        {
            "dir": str(directory),
            "files": files,
            "lines": lines,
            "oversized": files > MAX_FILES or lines > MAX_LINES,
            "empty": files == 0,
        }
    )
    return result


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

    snap = sub.add_parser(
        "snapshot",
        parents=[common],
        help="commit the working tree to a dangling object without touching refs",
    )
    snap.set_defaults(func=cmd_snapshot)

    begin = sub.add_parser(
        "begin-round", parents=[common], help="record the start of a review round"
    )
    begin.add_argument("--mode", required=True, choices=("deep", "followup"))
    begin.add_argument("--model", required=True)
    begin.add_argument("--snapshot", required=True, help="snapshot sha to review")
    begin.add_argument("--base-ref", required=True, help="ref the branch is reviewed against")
    begin.add_argument("--intent-file", help="file holding the change's stated intent")
    begin.add_argument(
        "--paths", nargs="+", help="glob patterns restricting the reviewed files"
    )
    begin.set_defaults(func=cmd_begin_round)
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
