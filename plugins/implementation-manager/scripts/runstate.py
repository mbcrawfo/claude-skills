"""A run's state: run.json under a lock, plus its units, gates and WIP limit."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re

STATE_FILE = "run.json"
LOCK_FILE = "run.lock"
REQUESTS = "requests.log"
PUSHES = "pushes.log"
GATES = "gates.log"

UNIT_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
SHA_RE = r"[0-9a-f]{40}"

METHODS = ("merge", "squash", "rebase")
QUEUE_MODES = ("serial", "parallel")


class RunError(Exception):
    """A run operation was refused; the message is the CLI's one stderr line."""


def _check_wip(wip: int | None) -> None:
    if wip is not None and wip < 1:
        raise RunError(f"wip must be at least 1 or none, got {wip}")


def _write_state(run_dir: Path, state: dict) -> None:
    tmp = run_dir / (STATE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, run_dir / STATE_FILE)


def init_run(run_dir: Path, *, repo: str, base: str, method: str, wip: int | None, queue_mode: str, now: float) -> dict:
    """Create the run folder with an empty state and logs. `now` is accepted for call symmetry and unused."""
    if method not in METHODS:
        raise RunError(f"method must be one of {', '.join(METHODS)}, got {method!r}")
    if queue_mode not in QUEUE_MODES:
        raise RunError(f"queue must be one of {', '.join(QUEUE_MODES)}, got {queue_mode!r}")
    _check_wip(wip)
    if (run_dir / STATE_FILE).exists():
        raise RunError(f"a run already exists in {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    state: dict = {
        "version": 1,
        "repo": repo,
        "base": base,
        "method": method,
        "queue_mode": queue_mode,
        "wip": wip,
        "units": {},
        "slot": None,
        "queue": [],
        "pending": [],
        "bad_requests": [],
        "requests_offset": 0,
        "marks": {},
    }
    for name in (LOCK_FILE, REQUESTS, PUSHES, GATES):
        (run_dir / name).touch()
    _write_state(run_dir, state)
    return state


def load(run_dir: Path) -> dict:
    """Read run.json without taking the lock."""
    try:
        with open(run_dir / STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise RunError(f"no run in {run_dir}") from None


@contextmanager
def locked(run_dir: Path) -> Iterator[dict]:
    """Hold the run lock, yield the state, and write it back atomically unless the block raises."""
    try:
        lock = open(run_dir / LOCK_FILE, "a")
    except FileNotFoundError:
        raise RunError(f"no run in {run_dir}") from None
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load(run_dir)
        yield state
        _write_state(run_dir, state)


def _unit(state: dict, name: str) -> dict:
    try:
        return state["units"][name]
    except KeyError:
        raise RunError(f"unknown unit {name!r}") from None


def add_unit(
    state: dict, name: str, *, prereqs: list[str] | None, branch: str | None, worktree: str | None, specs: bool | None
) -> None:
    """Add a unit, or update the fields given on an existing one; None keeps the old value."""
    if not re.fullmatch(UNIT_RE, name):
        raise RunError(
            f"bad unit name {name!r}: use letters, digits, '.', '_' and '-', starting with a letter or digit"
        )
    for prereq in prereqs or []:
        if prereq not in state["units"] or prereq == name:
            raise RunError(f"unit {name!r} has unknown prerequisite {prereq!r}")
    unit = state["units"].get(name)
    if unit is None:
        unit = state["units"][name] = {
            "order": len(state["units"]),
            "prereqs": [],
            "branch": None,
            "worktree": None,
            "specs": False,
            "gate": "closed",
            "gate_at": None,
            "done": False,
            "prs": {},
            "yielded": None,
        }
    for key, value in (("prereqs", prereqs), ("branch", branch), ("worktree", worktree), ("specs", specs)):
        if value is not None:
            unit[key] = list(value) if key == "prereqs" else value


def set_option(state: dict, key: str, value: str) -> None:
    """Set `wip` (an int of at least 1, or `none`) or `queue` (`serial` or `parallel`)."""
    if key == "wip":
        if value == "none":
            state["wip"] = None
            return
        if not re.fullmatch(r"[0-9]+", value) or int(value) < 1:
            raise RunError(f"wip must be a whole number of at least 1 or none, got {value!r}")
        state["wip"] = int(value)
    elif key == "queue":
        if value not in QUEUE_MODES:
            raise RunError(f"queue must be one of {', '.join(QUEUE_MODES)}, got {value!r}")
        state["queue_mode"] = value
    else:
        raise RunError(f"unknown option {key!r}: use wip or queue")


def openable(state: dict) -> list[str]:
    """Closed-gate units whose prerequisites are done, in order, cut to the free WIP; specs units are never cut."""
    units = state["units"]
    wip = state["wip"]
    in_flight = sum(1 for u in units.values() if u["gate"] == "open" and not u["done"] and not u["specs"])
    free = None if wip is None else max(wip - in_flight, 0)
    result = []
    for name, unit in sorted(units.items(), key=lambda item: item[1]["order"]):
        if unit["gate"] != "closed" or not all(units[p]["done"] for p in unit["prereqs"]):
            continue
        if unit["specs"]:
            result.append(name)
        elif free is None or free > 0:
            result.append(name)
            if free is not None:
                free -= 1
    return result


def _append(run_dir: Path, filename: str, line: str) -> None:
    with open(run_dir / filename, "a", encoding="utf-8") as f:
        f.write(line)


def open_gate(run_dir: Path, state: dict, unit: str, now: float) -> None:
    """Open a unit's gate and log it; opening twice is an error."""
    record = _unit(state, unit)
    if record["gate"] == "open":
        raise RunError(f"gate for {unit!r} is already open")
    _append(run_dir, GATES, f"{unit} open\n")
    record["gate"] = "open"
    record["gate_at"] = now


def stop_gate(run_dir: Path, state: dict, unit: str, reason: str) -> None:
    """Log that a unit has stopped, with a one-line reason."""
    _unit(state, unit)
    if "\n" in reason or "\r" in reason:
        raise RunError("a stop reason must be a single line")
    _append(run_dir, GATES, f"{unit} stop {reason}\n")
