"""The coverage verdict `run.py wait --full` prints under an event: branch-review's `rs covered` for its commit."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re
import subprocess

from runstate import PUSHES, REQUESTS, SHA_RE, UNIT_RE

TIMEOUT_SECONDS = 120

_TARGETS = (
    re.compile(rf"(?:REQUEST|PENDING) ({UNIT_RE}) ({SHA_RE})(?: |$)"),
    re.compile(rf"NEXT ({UNIT_RE}) ({SHA_RE})$"),
    re.compile(rf"(?:READY|REMIND) ({UNIT_RE}) \d+ ({SHA_RE})$"),
)
_STUCK = re.compile(rf"STUCK ({UNIT_RE})(?: |$)")


def event_target(line: str) -> tuple[str, str] | None:
    """The (unit, sha) a REQUEST, PENDING, NEXT, READY or REMIND event asks about; None for any other line."""
    for pattern in _TARGETS:
        match = pattern.match(line)
        if match:
            return match.group(1), match.group(2)
    return None


def _unavailable(sha: str, reason: str) -> str:
    return f"verdict {sha} unavailable: {reason}"


def verdict_line(state: dict, unit: str, sha: str, *, run: Callable = subprocess.run) -> str:
    """`verdict <sha> <compact JSON>` from `rs covered`, or `verdict <sha> unavailable: <reason>`."""
    check = state.get("check")
    if not check:
        return _unavailable(sha, "no check command recorded")
    if not isinstance(check, dict) or not all(isinstance(check.get(key), str) for key in ("rs", "state_dir")):
        return _unavailable(sha, "check command record is malformed; record it again with check-cmd")
    rs = check["rs"]
    if not Path(rs).is_file():
        return _unavailable(sha, f"rs not found: {rs}")
    worktree = (state["units"].get(unit) or {}).get("worktree")
    if not worktree:
        return _unavailable(sha, f"unit {unit} has no worktree")
    command = ["sh", rs, "covered", "--repo", worktree, "--commit", sha, "--state-dir", check["state_dir"]]
    try:
        result = run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return _unavailable(sha, "check timed out")
    except OSError as exc:
        return _unavailable(sha, f"check failed: {exc.strerror or exc}")
    if result.returncode != 0:
        lines = result.stderr.splitlines()
        return _unavailable(sha, f"check failed: {lines[0] if lines else f'exit {result.returncode}'}")
    try:
        obj = json.loads(result.stdout)
    except ValueError:
        return _unavailable(sha, "check printed no JSON")
    return f"verdict {sha} {json.dumps(obj, separators=(',', ':'))}"


def _last_line(path: Path, unit: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for text in reversed(lines):
        if text.split(" ", 1)[0] == unit:
            return text
    return None


def log_lines(run_dir: Path, line: str) -> list[str]:
    """For a STUCK event, the unit's last pushes.log and requests.log lines; [] for any other event."""
    match = _STUCK.match(line)
    if not match:
        return []
    unit = match.group(1)
    out = []
    for prefix, name in (("pushes", PUSHES), ("requests", REQUESTS)):
        last = _last_line(run_dir / name, unit)
        if last is not None:
            out.append(f"{prefix}: {last}")
    return out
