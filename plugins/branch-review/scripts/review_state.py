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
import shutil
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


# --- findings ledger -------------------------------------------------------

STATUSES = ("open", "fixed", "still-open", "verified", "invalid", "escalated", "deferred")
SEVERITIES = ("critical", "major", "minor")
CATEGORIES = (
    "logic",
    "sibling-path",
    "security",
    "tests",
    "intent",
    "conventions",
    "docs",
    "build-ci",
)
RISK_LEVELS = ("minimal", "low", "moderate", "high")
REACHABLE = ("yes", "no", "unclear")
EFFORTS = ("quick", "heavy")
VERDICT_STATUSES = ("verified", "still-open")
FINDING_REQUIRED = (
    "severity",
    "category",
    "file",
    "lines",
    "title",
    "trigger",
    "consequence",
    "reachable_now",
    "fix",
)
FINDING_OPTIONAL = ("reachable_note", "evidence", "test", "source")
MAX_ROUNDS = 6
_JSON_BLOCK = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


def extract_json_block(text: str) -> dict:
    """Parse the last fenced ```json block in a reviewer report."""
    blocks = _JSON_BLOCK.findall(text)
    if not blocks:
        raise ValueError("no json block")
    try:
        return json.loads(blocks[-1])
    except ValueError as exc:
        raise ValueError("json parse error: %s" % exc)


def _bad(message: str) -> StateError:
    return StateError("invalid reviewer output: %s" % message)


def _require_str(obj: dict, key: str, where: str) -> str:
    if key not in obj:
        raise _bad("%s is missing %s" % (where, key))
    if not isinstance(obj[key], str):
        raise _bad("%s.%s must be a string" % (where, key))
    return obj[key]


def _require_enum(obj: dict, key: str, allowed: Tuple[str, ...], where: str) -> str:
    value = _require_str(obj, key, where)
    if value not in allowed:
        raise _bad(
            "%s.%s must be one of %s, got %r" % (where, key, "|".join(allowed), value)
        )
    return value


def _require_list(obj: dict, key: str, where: str) -> list:
    if key not in obj:
        raise _bad("%s is missing %s" % (where, key))
    if not isinstance(obj[key], list):
        raise _bad("%s.%s must be a list" % (where, key))
    return obj[key]


def _optional_str(obj: dict, key: str, default: str, where: str) -> str:
    if key not in obj:
        return default
    if not isinstance(obj[key], str):
        raise _bad("%s.%s must be a string" % (where, key))
    return obj[key]


def _validate_output(data: object, known_ids: set) -> dict:
    """Check the reviewer block; return its verdicts, findings and outside entries."""
    if not isinstance(data, dict):
        raise _bad("expected a JSON object")
    _require_str(data, "summary", "output")
    risk = data.get("merge_risk")
    if not isinstance(risk, dict):
        raise _bad("output is missing merge_risk")
    _require_enum(risk, "level", RISK_LEVELS, "merge_risk")
    _require_str(risk, "reason", "merge_risk")
    check = data.get("intent_check")
    if not isinstance(check, dict):
        raise _bad("output is missing intent_check")
    _require_list(check, "unmet_claims", "intent_check")
    _require_list(check, "unmentioned_behavior", "intent_check")

    verdicts = []
    for i, v in enumerate(_require_list(data, "verdicts", "output")):
        where = "verdicts[%d]" % i
        if not isinstance(v, dict):
            raise _bad("%s must be an object" % where)
        vid = _require_str(v, "id", where)
        status = _require_enum(v, "status", VERDICT_STATUSES, where)
        if vid not in known_ids:
            raise _bad("%s refers to unknown finding %s" % (where, vid))
        verdicts.append(
            {"id": vid, "status": status, "reason": _optional_str(v, "reason", "", where)}
        )

    findings = []
    for i, f in enumerate(_require_list(data, "findings", "output")):
        where = "findings[%d]" % i
        if not isinstance(f, dict):
            raise _bad("%s must be an object" % where)
        rec = {}
        for key in FINDING_REQUIRED:
            if key == "severity":
                rec[key] = _require_enum(f, key, SEVERITIES, where)
            elif key == "category":
                rec[key] = _require_enum(f, key, CATEGORIES, where)
            elif key == "reachable_now":
                rec[key] = _require_enum(f, key, REACHABLE, where)
            else:
                rec[key] = _require_str(f, key, where)
        for key in FINDING_OPTIONAL:
            rec[key] = _optional_str(f, key, "", where)
        rec["effort"] = _require_enum(f, "effort", EFFORTS, where) if "effort" in f else "quick"
        findings.append(rec)

    outside = []
    for i, o in enumerate(_require_list(data, "outside_change", "output")):
        where = "outside_change[%d]" % i
        if not isinstance(o, dict):
            raise _bad("%s must be an object" % where)
        rec = {
            "severity": "minor",
            "category": "logic",
            "file": _require_str(o, "file", where),
            "lines": _optional_str(o, "lines", "", where),
            "title": _require_str(o, "title", where),
            "detail": _optional_str(o, "detail", "", where),
        }
        if "severity" in o:
            rec["severity"] = _require_enum(o, "severity", SEVERITIES, where)
        if "category" in o:
            rec["category"] = _require_enum(o, "category", CATEGORIES, where)
        outside.append(rec)

    return {"verdicts": verdicts, "findings": findings, "outside": outside}


def _load_output(path: str) -> object:
    """Parse a bare JSON file, else the last json block of a markdown report."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return extract_json_block(text)
    except ValueError as exc:
        raise _bad(str(exc))


def _ledger_state(args: argparse.Namespace) -> Tuple[Path, dict]:
    """Load the branch's state, which must exist and hold at least one round."""
    directory = state_path(Path(args.state_dir).expanduser(), Path(args.repo))
    state = load_state(directory)
    if state is None or not state.get("rounds"):
        raise StateError("no review round in progress; run begin-round first")
    state.setdefault("findings", [])
    return directory, state


def _find(state: dict, finding_id: str) -> dict:
    for f in state["findings"]:
        if f.get("id") == finding_id:
            return f
    raise StateError("unknown finding id %s" % finding_id)


def cmd_record_findings(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    rnd = state["rounds"][-1]
    number = rnd["number"]
    if "added" in rnd:
        raise StateError("round %d already has recorded findings" % number)
    known = {f.get("id") for f in state["findings"]}
    data = _load_output(args.file)
    checked = _validate_output(data, known)

    for v in checked["verdicts"]:
        f = _find(state, v["id"])
        f["status"] = v["status"]
        if v["status"] == "verified":
            f["round_closed"] = number
        if v["reason"]:
            f["discussion"].append(
                {"from": "reviewer", "text": v["reason"], "round": number}
            )

    new: List[dict] = []
    for rec in checked["findings"]:
        new.append(
            {
                "id": "R%d-F%d" % (number, len(new) + 1),
                "status": "open",
                "scope": "change",
                "round_opened": number,
                "round_closed": None,
                **rec,
                "resolution": None,
                "discussion": [],
            }
        )
    added = len(new)
    for rec in checked["outside"]:
        new.append(
            {
                "id": "R%d-F%d" % (number, len(new) + 1),
                "status": "deferred",
                "scope": "outside",
                "round_opened": number,
                "round_closed": None,
                **rec,
                "resolution": {
                    "status": "deferred",
                    "note": "outside the change set",
                    "commit": "",
                    "by": "reviewer",
                    "round": number,
                },
                "discussion": [],
            }
        )
    state["findings"].extend(new)

    rnd["summary"] = data["summary"]
    rnd["merge_risk"] = data["merge_risk"]
    rnd["intent_check"] = data["intent_check"]
    rnd["added"] = added

    save_state(directory, state)
    if args.report:
        rounds_dir = directory / "rounds"
        rounds_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.file, str(rounds_dir / ("%d.md" % number)))
    return {
        "round": number,
        "added": added,
        "outside": len(new) - added,
        "ids": [f["id"] for f in new],
        "verdicts": len(checked["verdicts"]),
    }


def cmd_set_status(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    note = args.note or ""
    commit = args.commit or ""
    if args.status == "fixed" and not (commit or note):
        raise StateError("fixed requires --commit or --note")
    if args.status in ("invalid", "escalated") and not note:
        raise StateError("%s requires --note" % args.status)
    number = state["rounds"][-1]["number"]
    finding["status"] = args.status
    finding["resolution"] = {
        "status": args.status,
        "note": note,
        "commit": commit,
        "by": "orchestrator",
        "round": number,
    }
    finding["round_closed"] = number if args.status in ("invalid", "escalated") else None
    save_state(directory, state)
    return {
        "id": finding["id"],
        "status": finding["status"],
        "round_closed": finding["round_closed"],
    }


def cmd_discuss(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    number = state["rounds"][-1]["number"]
    finding["discussion"].append({"from": args.source, "text": args.text, "round": number})
    save_state(directory, state)
    return {"id": finding["id"], "discussion": len(finding["discussion"])}


def cmd_resolve(args: argparse.Namespace) -> dict:
    directory, state = _ledger_state(args)
    finding = _find(state, args.id)
    if finding["status"] != "escalated":
        raise StateError("%s is %s, not escalated" % (finding["id"], finding["status"]))
    number = state["rounds"][-1]["number"]
    finding["status"] = "open" if args.decision == "fix" else "deferred"
    finding["resolution"] = {
        "status": finding["status"],
        "note": args.note,
        "commit": "",
        "by": "developer",
        "round": number,
    }
    finding["round_closed"] = number if args.decision == "defer" else None
    save_state(directory, state)
    return {
        "id": finding["id"],
        "status": finding["status"],
        "round_closed": finding["round_closed"],
    }


def cmd_status(args: argparse.Namespace) -> dict:
    state = load_state(state_path(Path(args.state_dir).expanduser(), Path(args.repo)))
    if state is None or not state.get("rounds"):
        raise StateError("no review rounds recorded for this branch")
    rounds = state["rounds"]
    counts = {s: 0 for s in STATUSES}
    for f in state.get("findings", []):
        counts[f.get("status")] = counts.get(f.get("status"), 0) + 1

    if len(rounds) >= MAX_ROUNDS:
        cont, reason = False, "max-rounds"
    elif counts["open"] or counts["still-open"]:
        cont, reason = True, "open-findings"
    elif counts["fixed"]:
        cont, reason = True, "unverified-fixes"
    elif rounds[-1].get("added", 0) > 0:
        cont, reason = True, "new-findings"
    else:
        cont, reason = False, "converged"
    return {
        "rounds": len(rounds),
        "last_mode": rounds[-1]["mode"],
        "counts": counts,
        "continue": cont,
        "reason": reason,
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

    rec = sub.add_parser(
        "record-findings", parents=[common], help="record a reviewer report into the ledger"
    )
    rec.add_argument("--file", required=True, help="reviewer report (markdown) or bare JSON")
    rec.add_argument("--report", action="store_true", help="keep a copy as rounds/<N>.md")
    rec.set_defaults(func=cmd_record_findings)

    setst = sub.add_parser("set-status", parents=[common], help="update a finding's status")
    setst.add_argument("--id", required=True)
    setst.add_argument(
        "--status", required=True, choices=("fixed", "invalid", "escalated", "open")
    )
    setst.add_argument("--commit", help="commit that fixes the finding")
    setst.add_argument("--note", help="explanation")
    setst.set_defaults(func=cmd_set_status)

    disc = sub.add_parser("discuss", parents=[common], help="append a discussion entry")
    disc.add_argument("--id", required=True)
    disc.add_argument(
        "--from", dest="source", required=True, choices=("orchestrator", "reviewer")
    )
    disc.add_argument("--text", required=True)
    disc.set_defaults(func=cmd_discuss)

    res = sub.add_parser("resolve", parents=[common], help="record the developer's decision")
    res.add_argument("--id", required=True)
    res.add_argument("--decision", required=True, choices=("fix", "defer"))
    res.add_argument("--note", required=True, help="the decision, in the developer's words")
    res.set_defaults(func=cmd_resolve)

    stat = sub.add_parser("status", parents=[common], help="counts and whether to continue")
    stat.set_defaults(func=cmd_status)
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
