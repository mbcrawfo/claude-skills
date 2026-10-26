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

# The watcher's mark for the last NEXT it reported; not a unit name, since unit names start with a letter or digit.
NEXT_MARK = "_next"

# No '.': children match their unit name in `grep -E` patterns, where it would be a wildcard.
UNIT_RE = r"[A-Za-z0-9][A-Za-z0-9_-]*"
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


STATE_KEYS = (
    "version",
    "repo",
    "base",
    "method",
    "queue_mode",
    "wip",
    "units",
    "slot",
    "queue",
    "pending",
    "bad_requests",
    "requests_offset",
    "marks",
)


def load(run_dir: Path) -> dict:
    """Read run.json without taking the lock; refuse one that is not a version 1 run state.

    Raises json.JSONDecodeError on a file that is not JSON.
    """
    path = run_dir / STATE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        raise RunError(f"no run in {run_dir}") from None
    if not isinstance(state, dict) or state.get("version") != 1:
        raise RunError(f"{path} is not a version 1 run state")
    missing = [key for key in STATE_KEYS if key not in state]
    if missing:
        raise RunError(f"{path} is missing {', '.join(missing)}")
    for unit in state["units"].values():  # fields added since a run began
        unit.setdefault("after", [])
        unit.setdefault("hold", None)
        unit.setdefault("title", None)
    return state


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


def _check_order_edges(state: dict, name: str, prereqs: list[str], after: list[str]) -> None:
    """Refuse a unit whose prerequisites or merge-order units are unknown, overlap, or lead back to the unit."""
    units = state["units"]
    for prereq in prereqs:
        if prereq not in units or prereq == name:
            raise RunError(f"unit {name!r} has unknown prerequisite {prereq!r}")
    for other in after:
        if other not in units or other == name:
            raise RunError(f"unit {name!r} has unknown merge-order unit {other!r}")
    for other in after:
        if other in prereqs:
            raise RunError(f"unit {name!r} cannot both need and merge after {other!r}")
    for start in [*prereqs, *after]:
        seen: set[str] = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current == name:
                raise RunError(f"unit {name!r} would wait on itself through {start!r}")
            if current not in seen:
                seen.add(current)
                pending += units[current]["prereqs"] + units[current]["after"]


def add_unit(
    state: dict,
    name: str,
    *,
    prereqs: list[str] | None,
    branch: str | None,
    worktree: str | None,
    specs: bool | None,
    after: list[str] | None = None,
    title: str | None = None,
) -> None:
    """Add a unit, or update the fields given on an existing one; None keeps the old value.

    `after` lists the units that must merge before this one does; [] clears it. A unit may not wait on itself
    through its prerequisites and `after` units together.
    """
    if not re.fullmatch(UNIT_RE, name):
        raise RunError(f"bad unit name {name!r}: use letters, digits, '_' and '-', starting with a letter or digit")
    unit = state["units"].get(name)
    _check_order_edges(
        state,
        name,
        prereqs if prereqs is not None else (unit["prereqs"] if unit else []),
        after if after is not None else (unit["after"] if unit else []),
    )
    if unit is None:
        unit = state["units"][name] = {
            "order": len(state["units"]),
            "prereqs": [],
            "after": [],
            "hold": None,
            "title": None,
            "branch": None,
            "worktree": None,
            "specs": False,
            "gate": "closed",
            "gate_at": None,
            "done": False,
            "prs": {},
        }
    for key, value in (
        ("prereqs", prereqs),
        ("after", after),
        ("branch", branch),
        ("worktree", worktree),
        ("specs", specs),
        ("title", title),
    ):
        if value is not None:
            unit[key] = list(value) if key in ("prereqs", "after") else value


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


def mergeable(state: dict, unit: str) -> bool:
    """True when the unit is not held and every unit it merges after is done or stopped."""
    record = _unit(state, unit)
    units = state["units"]
    return record["hold"] is None and all(units[u]["done"] or units[u]["gate"] == "stopped" for u in record["after"])


def openable(state: dict) -> list[str]:
    """Closed-gate units whose prerequisites are done, in order, cut to the free WIP.

    Specs units are never cut, nor is a unit that an open, unmerged unit merges after: it must open for that unit
    to merge, so it does not wait on or use up the WIP.
    """
    units = state["units"]
    merged_after_by_open = {
        other for u in units.values() if u["gate"] == "open" and not u["done"] for other in u["after"]
    }
    wip = state["wip"]
    in_flight = sum(1 for u in units.values() if u["gate"] == "open" and not u["done"] and not u["specs"])
    free = None if wip is None else max(wip - in_flight, 0)
    result = []
    for name, unit in sorted(units.items(), key=lambda item: item[1]["order"]):
        if unit["gate"] != "closed" or not all(units[p]["done"] for p in unit["prereqs"]):
            continue
        if unit["specs"] or name in merged_after_by_open:
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
    _refuse_stopped(record, unit)
    _append(run_dir, GATES, f"{unit} open\n")
    record["gate"] = "open"
    record["gate_at"] = now


def stop_gate(run_dir: Path, state: dict, unit: str, reason: str) -> None:
    """Log that a unit stopped, with a one-line reason.

    Its gate is now `stopped`: it never opens, counts to WIP, gets `go` or joins the queue. It gives up the slot,
    its hold is lifted, and its queued and pending requests are dropped.
    """
    record = _unit(state, unit)
    if "\n" in reason or "\r" in reason:
        raise RunError("a stop reason must be a single line")
    _append(run_dir, GATES, f"{unit} stop {reason}\n")
    record["gate"] = "stopped"
    record["hold"] = None
    if _holds_slot(state, unit):
        state["slot"] = None
    _drop_entries(state, unit, "pending", "queue")
    state["marks"].get(unit, {}).pop("go_head", None)  # a stopped unit is not pushing


def _refuse_stopped(record: dict, unit: str) -> None:
    if record["gate"] == "stopped":
        raise RunError(f"unit {unit!r} is stopped; answer its push requests with: redo <sha> unit-stopped")


def has_open_pr(state: dict, unit: str) -> bool:
    """True when any of the unit's PRs is still open."""
    return any(status == "OPEN" for status in _unit(state, unit)["prs"].values())


def _check_sha(sha: str) -> None:
    if not re.fullmatch(SHA_RE, sha):
        raise RunError(f"sha must be a full 40-character lowercase hex commit id, got {sha!r}")


def _drop_entries(state: dict, unit: str, *keys: str) -> None:
    for key in keys:
        state[key] = [entry for entry in state[key] if entry["unit"] != unit]


def _holds_slot(state: dict, unit: str) -> bool:
    return state["slot"] is not None and state["slot"]["unit"] == unit


def _first_queued(state: dict) -> str | None:
    """The unit first in the queue, skipping done units, whose entries the watcher drops, and units not mergeable."""
    for entry in queue_order(state):
        if not state["units"][entry["unit"]]["done"] and mergeable(state, entry["unit"]):
            return entry["unit"]
    return None


def _note_answered(run_dir: Path, state: dict, unit: str, sha: str) -> None:
    """Remember that the unit's requests for `sha` written so far are answered.

    The manager may answer a request before any wait has read it. The watcher skips such a line, one that ends
    at or before requests.log's size at the answer, instead of taking it as a new, unanswered request. A request
    for the same sha written after the answer is new.
    """
    try:
        size = (run_dir / REQUESTS).stat().st_size
    except FileNotFoundError:
        return
    if size > state["requests_offset"]:  # otherwise every request so far has been read
        state["marks"].setdefault(unit, {}).setdefault("answered", {})[sha] = size


def go(run_dir: Path, state: dict, unit: str, sha: str, now: float) -> None:
    """Answer a push request with `go`: log it and, in serial mode, take the slot.

    It refuses a held unit. In serial mode it also refuses while the unit merges after an unmerged unit, while
    another unit holds the slot, or while the slot is free and another unit is first in the queue.
    """
    record = _unit(state, unit)
    _check_sha(sha)
    if record["done"]:
        raise RunError(f"unit {unit!r} is done; answer its push requests with: redo <sha> pr-merged")
    _refuse_stopped(record, unit)
    if record["hold"] is not None:
        reason = record["hold"]["reason"]
        raise RunError(f"unit {unit!r} is held: {reason}; answer its push requests with: redo <sha> held")
    if state["queue_mode"] == "serial":
        units = state["units"]
        for other in record["after"]:
            if not units[other]["done"] and units[other]["gate"] != "stopped":
                raise RunError(f"unit {unit!r} merges after {other!r}; use queue to line it up until it merges")
        holder = state["slot"]["unit"] if state["slot"] else None
        if holder not in (None, unit):
            raise RunError(f"the push slot is held by {holder!r}; use queue to line {unit!r} up behind it")
        first = _first_queued(state) if holder is None else None
        if first not in (None, unit):
            raise RunError(f"{first!r} is first in the queue; use queue to line {unit!r} up behind it")
    _append(run_dir, PUSHES, f"{unit} go {sha}\n")
    _note_answered(run_dir, state, unit, sha)
    _drop_entries(state, unit, "pending", "queue")
    marks = state["marks"].setdefault(unit, {})
    if marks.get("head") == sha:
        # The approved commit is already the PR's head: there is nothing new to wait for.
        marks.pop("go_head", None)
    else:
        # The watcher holds READY and REMIND while the PR's head is still this one, until the push lands.
        marks["go_head"] = marks.get("head")
    if state["queue_mode"] == "serial":
        state["slot"] = {"unit": unit, "sha": sha, "at": now, "head": marks.get("head")}
        # The watcher's last NEXT has been answered; when the slot frees, the queue head is reported afresh.
        state["marks"].pop(NEXT_MARK, None)


def redo(run_dir: Path, state: dict, unit: str, sha: str, reasons: str) -> None:
    """Answer a push request with `redo` and one line of reasons; the unit gives up the slot.

    Any earlier `go` is spent: the child asks again, so the watcher stops holding READY for a push.
    """
    _unit(state, unit)
    _check_sha(sha)
    if not reasons.strip() or "\n" in reasons or "\r" in reasons:
        raise RunError("redo reasons must be a single non-empty line")
    _append(run_dir, PUSHES, f"{unit} redo {sha} {reasons}\n")
    _note_answered(run_dir, state, unit, sha)
    _drop_entries(state, unit, "pending", "queue")
    state["marks"].get(unit, {}).pop("go_head", None)
    if _holds_slot(state, unit):
        state["slot"] = None


def enqueue(run_dir: Path, state: dict, unit: str, sha: str, now: float) -> None:
    """Line a unit's request up behind the slot holder, replacing any earlier entry; the same sha keeps its time."""
    _refuse_stopped(_unit(state, unit), unit)
    _check_sha(sha)
    if state["queue_mode"] == "parallel":
        raise RunError("queue is not used in parallel mode; answer with go")
    if _holds_slot(state, unit):
        raise RunError(f"unit {unit!r} already holds the push slot")
    # The same sha queued again (a NEXT whose go was refused) keeps its place.
    at = next((entry["at"] for entry in state["queue"] if entry["unit"] == unit and entry["sha"] == sha), now)
    _drop_entries(state, unit, "pending", "queue")
    _note_answered(run_dir, state, unit, sha)
    state["queue"].append({"unit": unit, "sha": sha, "at": at, "front": has_open_pr(state, unit)})


def queue_order(state: dict) -> list[dict]:
    """Queue entries in service order: units with an open PR first, then the rest, each by time."""
    entries = sorted(state["queue"], key=lambda entry: entry["at"])
    return [e for e in entries if e["front"]] + [e for e in entries if not e["front"]]


def yield_slot(state: dict, unit: str, reason: str, now: float) -> None:
    """The slot holder gives the slot up. `reason` (for the manager's transcript) and `now` are unused.

    A `go` it has not pushed yet stays outstanding: the child may still push it, so the watcher keeps holding
    READY for the old head until a new head appears or the unit asks again.
    """
    _unit(state, unit)
    if not _holds_slot(state, unit):
        raise RunError(f"unit {unit!r} does not hold the push slot")
    state["slot"] = None


def hold(run_dir: Path, state: dict, unit: str, reason: str, now: float) -> None:
    """Hold a unit's merge, with a one-line reason, until `release`, `merged`, `closed` or `stop`.

    A held unit is not mergeable: it is not reported READY or REMIND, not STALLED, and never heads the queue, and
    `go` refuses it. Its unanswered requests are answered `redo held`, and it gives up the queue and the slot. A
    `go` it has not pushed yet stays outstanding, as for `yield`.
    """
    record = _unit(state, unit)
    if "\n" in reason or "\r" in reason or not reason.strip():
        raise RunError("a hold reason must be a single non-empty line")
    if record["done"]:
        raise RunError(f"unit {unit!r} is done and cannot be held")
    _refuse_stopped(record, unit)
    if record["hold"] is not None:
        raise RunError(f"unit {unit!r} is already held: {record['hold']['reason']}")
    for entry in [e for e in state["pending"] if e["unit"] == unit]:
        _append(run_dir, PUSHES, f"{unit} redo {entry['sha']} held\n")
        _note_answered(run_dir, state, unit, entry["sha"])
    _drop_entries(state, unit, "pending", "queue")
    if _holds_slot(state, unit):
        state["slot"] = None
    record["hold"] = {"reason": reason, "at": now}


def release(state: dict, unit: str) -> None:
    """Lift a unit's hold; it is mergeable again, and the watcher reports it afresh."""
    record = _unit(state, unit)
    if record["hold"] is None:
        raise RunError(f"unit {unit!r} is not held")
    record["hold"] = None


def record_merged(state: dict, unit: str, pr: int, *, more: bool) -> None:
    """Note a merged PR: free the slot if held, lift any hold, and mark the unit done unless more PRs are coming."""
    record = _unit(state, unit)
    record["prs"][str(pr)] = "MERGED"
    record["hold"] = None
    if _holds_slot(state, unit):
        state["slot"] = None
    record["done"] = not more


def record_closed(state: dict, unit: str, pr: int) -> None:
    """Note a PR closed without merging, lift any hold, and free the slot if the unit holds it."""
    record = _unit(state, unit)
    record["prs"][str(pr)] = "CLOSED"
    record["hold"] = None
    if _holds_slot(state, unit):
        state["slot"] = None


def reportable(state: dict, unit: str) -> bool:
    """True when the unit may be reported on (READY/REMIND): it is not stopped, it is mergeable, and the run is in
    parallel mode or the slot is free or its own."""
    if _unit(state, unit)["gate"] == "stopped" or not mergeable(state, unit):
        return False
    return state["queue_mode"] == "parallel" or state["slot"] is None or state["slot"]["unit"] == unit
