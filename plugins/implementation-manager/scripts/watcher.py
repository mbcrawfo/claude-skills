"""The run watcher: block until one event for the manager, print it as one line, and exit."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Callable

from runstate import REQUESTS, SHA_RE, UNIT_RE, locked, queue_order

REQUEST_RE = rf"^({UNIT_RE}) push ({SHA_RE}) (\S+)$"

BAD_REQUESTS_KEPT = 20
NEXT_MARK = "_next"


def _note_bad(state: dict, line: str) -> None:
    state["bad_requests"] = (state["bad_requests"] + [line])[-BAD_REQUESTS_KEPT:]


def take_request(run_dir: Path, state: dict, now: float) -> str | None:
    """Consume whole requests.log lines up to the first valid request and report it as `REQUEST <unit> <sha> <branch>`.

    Lines that are not a valid request for a known unit go to `bad_requests`; a trailing line without its
    newline is left for a later call. A request from a done unit is still reported.
    """
    path = run_dir / REQUESTS
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    offset = state["requests_offset"]
    if size < offset:
        _note_bad(state, f"{REQUESTS} was truncated")
        offset = state["requests_offset"] = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    # Split on b"\n" only: bytes.splitlines would also break at a bare "\r" and stall on that fragment.
    for raw in data.split(b"\n")[:-1]:
        offset += len(raw) + 1
        state["requests_offset"] = offset
        line = raw.decode("utf-8", errors="replace")
        match = re.fullmatch(REQUEST_RE, line)
        if not match or match.group(1) not in state["units"]:
            _note_bad(state, line)
            continue
        unit, sha, branch = match.groups()
        state["pending"] = [entry for entry in state["pending"] if entry["unit"] != unit]
        state["pending"].append({"unit": unit, "sha": sha, "branch": branch, "at": now})
        return f"REQUEST {unit} {sha} {branch}"
    return None


def next_event(state: dict) -> str | None:
    """In serial mode with the slot free, report the head of the queue as `NEXT <unit> <sha>`, once per (unit, sha).

    Queue entries for done units are dropped first.
    """
    if state["queue_mode"] != "serial" or state["slot"] is not None:
        return None
    state["queue"] = [entry for entry in state["queue"] if not state["units"][entry["unit"]]["done"]]
    if not state["queue"]:
        return None
    head = queue_order(state)[0]
    key = [head["unit"], head["sha"]]
    if state["marks"].get(NEXT_MARK) == key:
        return None
    state["marks"][NEXT_MARK] = key
    return f"NEXT {head['unit']} {head['sha']}"


def _poll_prs(run_dir: Path, *, gh: Callable[[list[str]], str], now: float) -> str | None:
    """PR polling seam: polls each unit's PR when due and returns a PR event line. A no-op until PR events land."""
    return None


def wait(
    run_dir: Path,
    *,
    gh: Callable[[list[str]], str],
    sleep: Callable[[float], None],
    now: Callable[[], float],
    request_seconds: float = 5.0,
    poll_seconds: float = 60.0,
    confirm_seconds: float = 30.0,
    stall_seconds: float = 10800.0,
    stuck_seconds: float = 1200.0,
    no_push_seconds: float = 1800.0,
    remind_seconds: float = 1800.0,
) -> str:
    """Loop until there is one event for the manager and return its line.

    Each step takes the run lock on its own, so the lock is never held across a sleep or a gh call and
    state changes the manager makes between steps are kept.
    """
    while True:
        with locked(run_dir) as state:
            line = take_request(run_dir, state, now())
        if line:
            return line
        line = _poll_prs(run_dir, gh=gh, now=now())
        if line:
            return line
        with locked(run_dir) as state:
            line = next_event(state)
        if line:
            return line
        sleep(request_seconds)
