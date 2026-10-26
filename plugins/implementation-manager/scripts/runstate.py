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

# A unit's mark: it lost the slot when it gained --after, and has not asked to push since.
FREED_MARK = "freed_for_after"

# The watcher's mark for the last NEXT it reported; not a unit name, since unit names start with a letter or digit.
NEXT_MARK = "_next"

# No '.': children match their unit name in `grep -E` patterns, where it would be a wildcard.
UNIT_RE = r"[A-Za-z0-9][A-Za-z0-9_-]*"
SHA_RE = r"[0-9a-f]{40}"

METHODS = ("merge", "squash", "rebase")
QUEUE_MODES = ("serial", "parallel")
FINAL_MERGES = ("manager", "owner")


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


def init_run(
    run_dir: Path,
    *,
    repo: str,
    base: str,
    method: str,
    wip: int | None,
    queue_mode: str,
    now: float,
    integration: dict | None = None,
) -> dict:
    """Create the run folder with an empty state and logs. `now` is accepted for call symmetry and unused.

    `integration` ({"branch", "final_merge", "feature_review"}) turns on integration mode; it is stored with the
    final gate closed. A run without it has no `integration` key, as before.
    """
    if method not in METHODS:
        raise RunError(f"method must be one of {', '.join(METHODS)}, got {method!r}")
    if queue_mode not in QUEUE_MODES:
        raise RunError(f"queue must be one of {', '.join(QUEUE_MODES)}, got {queue_mode!r}")
    _check_wip(wip)
    if integration is not None:
        missing = [key for key in ("branch", "final_merge", "feature_review") if key not in integration]
        if missing:
            raise RunError(f"integration is missing {', '.join(missing)}")
        branch = integration["branch"]
        if not isinstance(branch, str) or not branch or re.search(r"\s", branch):
            raise RunError(f"integration branch must be non-empty with no whitespace, got {branch!r}")
        if integration["final_merge"] not in FINAL_MERGES:
            raise RunError(f"final-merge must be one of {', '.join(FINAL_MERGES)}, got {integration['final_merge']!r}")
        if not isinstance(integration["feature_review"], bool):
            raise RunError(f"feature_review must be true or false, got {integration['feature_review']!r}")
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
    if integration is not None:
        state["integration"] = {
            "branch": integration["branch"],
            "final_merge": integration["final_merge"],
            "feature_review": integration["feature_review"],
            "final_gate": False,
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
    state.setdefault("integration", None)
    for unit in state["units"].values():  # fields added since a run began
        unit.setdefault("integration", False)
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


def _block(state: dict) -> dict | None:
    """The integration config, or None when the run is not in the integration mode.

    A state straight from `init_run` or `add_unit` may lack the key that `load` fills in, so it is read with `get`.
    """
    return state.get("integration")


def _is_integration(unit: dict) -> bool:
    return unit.get("integration", False)


def unit_base(state: dict) -> str:
    """The branch units are cut from and merge into: the integration branch in the mode, else the base."""
    block = _block(state)
    return block["branch"] if block else state["base"]


def integration_unit(state: dict) -> str | None:
    """The name of the integration unit, if one was added."""
    return next((name for name, unit in state["units"].items() if _is_integration(unit)), None)


def _unit(state: dict, name: str) -> dict:
    try:
        return state["units"][name]
    except KeyError:
        raise RunError(f"unknown unit {name!r}") from None


def _refuse_integration_edge(units: dict, name: str, other: str) -> None:
    if _is_integration(units[other]):
        raise RunError(f"unit {name!r} cannot wait on {other!r}: no unit may wait on the integration unit")


def _check_order_edges(state: dict, name: str, prereqs: list[str], after: list[str]) -> None:
    """Refuse a unit whose prerequisites or merge-order units are unknown, overlap, or lead back to the unit."""
    units = state["units"]
    for prereq in prereqs:
        if prereq not in units or prereq == name:
            raise RunError(f"unit {name!r} has unknown prerequisite {prereq!r}")
        _refuse_integration_edge(units, name, prereq)
    for other in after:
        if other not in units or other == name:
            raise RunError(f"unit {name!r} has unknown merge-order unit {other!r}")
        _refuse_integration_edge(units, name, other)
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
    integration: bool | None = None,
) -> None:
    """Add a unit, or update the fields given on an existing one; None keeps the old value.

    `after` lists the units that must merge before this one does; [] clears it. A unit may not wait on itself
    through its prerequisites and `after` units together. `title` is a one-line display label for status.
    `integration=True` makes it the run's one integration unit: it needs the integration mode, branches from the
    base so it has no prerequisites or `after` units, and no other unit may wait on it. Its branch defaults to
    the integration branch and may be nothing else.
    A slot holder that can no longer merge gives the slot up, as for `hold`: it would block the units it waits for.
    """
    if not re.fullmatch(UNIT_RE, name):
        raise RunError(f"bad unit name {name!r}: use letters, digits, '_' and '-', starting with a letter or digit")
    if title is not None and ("\n" in title or "\r" in title):
        raise RunError(f"bad title {title!r}: use a single line")
    unit = state["units"].get(name)
    _check_order_edges(
        state,
        name,
        prereqs if prereqs is not None else (unit["prereqs"] if unit else []),
        after if after is not None else (unit["after"] if unit else []),
    )
    if integration is False and unit and _is_integration(unit):
        raise RunError("the integration unit cannot be changed back")
    if integration or (unit and _is_integration(unit)):
        _check_integration_unit(state, name, unit, prereqs, after, branch)
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
        if _block(state):
            unit["integration"] = False
    for key, value in (
        ("prereqs", prereqs),
        ("after", after),
        ("branch", branch),
        ("worktree", worktree),
        ("specs", specs),
        ("integration", integration or None),  # False is the default and not written
        ("title", title),
    ):
        if value is not None:
            unit[key] = list(value) if key in ("prereqs", "after") else value
    if _is_integration(unit) and unit["branch"] is None:
        unit["branch"] = _block(state)["branch"]
    if _holds_slot(state, name) and not mergeable(state, name):
        state["slot"] = None  # a `go` not pushed yet stays outstanding, as for `hold`
        # Nothing will make its child ask again: status says when it may, once its --after units merge.
        state["marks"].setdefault(name, {})[FREED_MARK] = True


def _check_integration_unit(
    state: dict,
    name: str,
    unit: dict | None,
    prereqs: list[str] | None,
    after: list[str] | None,
    branch: str | None,
) -> None:
    """Refuse a unit that is (or becomes) the integration unit unless it can be the run's one."""
    if not _block(state):
        raise RunError("run has no integration branch")
    other = integration_unit(state)
    if other not in (None, name):
        raise RunError(f"run already has an integration unit, {other!r}")
    if (prereqs if prereqs is not None else unit and unit["prereqs"]) or (
        after if after is not None else unit and unit["after"]
    ):
        raise RunError("the integration unit has no prerequisites or merge-order units")
    wanted = _block(state)["branch"]
    effective = branch if branch is not None else (unit["branch"] if unit else None)
    if effective not in (None, wanted):
        raise RunError(f"the integration unit's branch is {wanted!r}, not {effective!r}")
    for waiter, record in state["units"].items():
        if name in record["prereqs"] or name in record["after"]:
            raise RunError(f"unit {waiter!r} waits on {name!r}: no unit may wait on the integration unit")


def set_option(state: dict, key: str, value: str) -> None:
    """Set `wip` (an int of at least 1, or `none`), `queue` (`serial` or `parallel`), or, in the integration mode
    and before the final gate opens, `final-merge` (`manager` or `owner`) or `feature-review` (`yes` or `no`)."""
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
    elif key in ("final-merge", "feature-review"):
        block = _block(state)
        if not block:
            raise RunError(f"{key} needs a run with an integration branch")
        if block["final_gate"]:
            raise RunError(f"{key} cannot change: the final gate is open")
        if key == "final-merge":
            if value not in FINAL_MERGES:
                raise RunError(f"final-merge must be one of {', '.join(FINAL_MERGES)}, got {value!r}")
            block["final_merge"] = value
        else:
            if value not in ("yes", "no"):
                raise RunError(f"feature-review must be yes or no, got {value!r}")
            block["feature_review"] = value == "yes"
    else:
        raise RunError(f"unknown option {key!r}: use wip, queue, final-merge or feature-review")


def mergeable(state: dict, unit: str) -> bool:
    """True when the unit is not held and every unit it merges after is done or stopped."""
    record = _unit(state, unit)
    units = state["units"]
    return record["hold"] is None and all(units[u]["done"] or units[u]["gate"] == "stopped" for u in record["after"])


def _merge_targets(units: dict, active: set[str]) -> set[str]:
    """Units the active units must wait to merge: their --after units and all those need or merge after, unstopped."""
    pending = [other for name in active for other in units[name]["after"]]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        # A stopped unit counts as merged, so nothing waits on it or on what it needs.
        if current not in seen and units[current]["gate"] != "stopped":
            seen.add(current)
            pending += units[current]["prereqs"] + units[current]["after"]
    return {name for name in seen if not units[name]["done"]}


def openable(state: dict) -> list[str]:
    """Closed-gate units whose prerequisites are done, in order, cut to the free WIP.

    Specs units and the integration unit are never cut, nor is a unit that an open (or opening) unmerged unit must
    wait to merge: its --after units, and everything those need or merge after. It must open for that unit to merge,
    so it does not wait on or use up the WIP. Each unit that opens can exempt more, so the list is built until
    nothing more is added.
    """
    units = state["units"]
    active = {name for name, u in units.items() if u["gate"] == "open" and not u["done"]}
    wip = state["wip"]
    in_flight = sum(1 for name in active if not (units[name]["specs"] or _is_integration(units[name])))
    free = None if wip is None else max(wip - in_flight, 0)
    result: list[str] = []
    added = True
    while added:
        added = False
        exempt = _merge_targets(units, active)
        for name, unit in sorted(units.items(), key=lambda item: item[1]["order"]):
            if name in result or unit["gate"] != "closed" or not all(units[p]["done"] for p in unit["prereqs"]):
                continue
            if unit["specs"] or _is_integration(unit) or name in exempt:
                pass
            elif free is None or free > 0:
                if free is not None:
                    free -= 1
            else:
                continue
            result.append(name)
            active.add(name)
            added = True
    return sorted(result, key=lambda name: units[name]["order"])


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


def late_units(state: dict) -> list[str]:
    """Units other than the integration unit that are neither done nor stopped, in order."""
    units = state["units"]
    return [
        name
        for name, unit in sorted(units.items(), key=lambda item: item[1]["order"])
        if not _is_integration(unit) and not unit["done"] and unit["gate"] != "stopped"
    ]


def final_due(state: dict) -> bool:
    """True when the final gate should open: the integration unit's gate is open, every other unit is done or
    stopped, and at least one is done."""
    block = _block(state)
    name = integration_unit(state)
    if not block or block["final_gate"] or name is None or state["units"][name]["gate"] != "open":
        return False
    return not late_units(state) and any(u["done"] for u in state["units"].values() if not _is_integration(u))


def all_stopped(state: dict) -> bool:
    """True in the integration mode when there are units besides the integration unit and all are stopped."""
    if not _block(state):
        return False
    others = [u for u in state["units"].values() if not _is_integration(u)]
    return bool(others) and all(u["gate"] == "stopped" for u in others)


def open_final(run_dir: Path, state: dict) -> str:
    """Open the final gate and log it; returns the log line, without its newline. Refused unless `final_due`."""
    block = _block(state)
    if not final_due(state):
        raise RunError(
            "the final gate is already open" if block and block["final_gate"] else "the final gate is not due"
        )
    line = f"{integration_unit(state)} final {'review' if block['feature_review'] else 'no-review'}"
    _append(run_dir, GATES, line + "\n")
    block["final_gate"] = True
    return line


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
    _forget_freed(state, unit)
    for key in ("go_head", "go_sha"):  # a stopped unit is not pushing
        state["marks"].get(unit, {}).pop(key, None)


def _refuse_stopped(record: dict, unit: str) -> None:
    if record["gate"] == "stopped":
        raise RunError(f"unit {unit!r} is stopped; answer its push requests with: redo <sha> unit-stopped")


def _refuse_held(record: dict, unit: str) -> None:
    if record["hold"] is not None:
        reason = record["hold"]["reason"]
        raise RunError(f"unit {unit!r} is held: {reason}; answer its push requests with: redo <sha> held")


def has_open_pr(state: dict, unit: str) -> bool:
    """True when any of the unit's PRs is still open."""
    return any(status == "OPEN" for status in _unit(state, unit)["prs"].values())


def _check_sha(sha: str) -> None:
    if not re.fullmatch(SHA_RE, sha):
        raise RunError(f"sha must be a full 40-character lowercase hex commit id, got {sha!r}")


def _forget_freed(state: dict, unit: str) -> None:
    """The unit asked again, merged, closed or stopped: status no longer needs to prompt it."""
    state["marks"].get(unit, {}).pop(FREED_MARK, None)


def _drop_entries(state: dict, unit: str, *keys: str) -> None:
    for key in keys:
        state[key] = [entry for entry in state[key] if entry["unit"] != unit]


def _holds_slot(state: dict, unit: str) -> bool:
    return state["slot"] is not None and state["slot"]["unit"] == unit


def queue_head(state: dict) -> dict | None:
    """The queue entry served next: the first in queue order whose unit is not done and is mergeable."""
    for entry in queue_order(state):
        if not state["units"][entry["unit"]]["done"] and mergeable(state, entry["unit"]):
            return entry
    return None


def _first_queued(state: dict) -> str | None:
    """The unit at the head of the queue, if any."""
    head = queue_head(state)
    return head["unit"] if head else None


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
    _refuse_held(record, unit)
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
    _forget_freed(state, unit)
    marks = state["marks"].setdefault(unit, {})
    if marks.get("head") == sha:
        # The approved commit is already the PR's head: there is nothing new to wait for.
        marks.pop("go_head", None)
    else:
        # The watcher holds READY and REMIND while the PR's head is still this one, until the push lands.
        marks["go_head"] = marks.get("head")
    if _is_integration(record):
        marks["go_sha"] = sha  # the integration unit pushes straight to the branch: unit merges wait for `synced`
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
    _forget_freed(state, unit)
    for key in ("go_head", "go_sha"):
        state["marks"].get(unit, {}).pop(key, None)
    if _holds_slot(state, unit):
        state["slot"] = None


def enqueue(run_dir: Path, state: dict, unit: str, sha: str, now: float) -> None:
    """Line a unit's request up behind the slot holder, replacing any earlier entry; the same sha keeps its time.

    A slot holder that can no longer merge (it gained an `--after` unit) gives the slot up and is queued.
    """
    record = _unit(state, unit)
    _refuse_stopped(record, unit)
    _refuse_held(record, unit)
    _check_sha(sha)
    if state["queue_mode"] == "parallel":
        raise RunError("queue is not used in parallel mode; answer with go")
    if _holds_slot(state, unit):
        if mergeable(state, unit):
            raise RunError(f"unit {unit!r} already holds the push slot")
        state["slot"] = None
    # The same sha queued again (a NEXT whose go was refused) keeps its place.
    at = next((entry["at"] for entry in state["queue"] if entry["unit"] == unit and entry["sha"] == sha), now)
    _drop_entries(state, unit, "pending", "queue")
    _forget_freed(state, unit)
    _note_answered(run_dir, state, unit, sha)
    state["queue"].append({"unit": unit, "sha": sha, "at": at, "front": has_open_pr(state, unit)})


def queue_order(state: dict) -> list[dict]:
    """Queue entries in service order: the integration unit's first, then units with an open PR, then the rest,
    each by time."""
    entries = sorted(state["queue"], key=lambda entry: entry["at"])
    name = integration_unit(state)
    first = [e for e in entries if e["unit"] == name]
    entries = [e for e in entries if e["unit"] != name]
    return first + [e for e in entries if e["front"]] + [e for e in entries if not e["front"]]


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

    A held unit is not mergeable: it is not reported READY or REMIND, not STALLED unless the reason starts with
    `answer sent:` (that hold waits on the child), and never heads the queue, and `go` and `queue` refuse it. Its
    pending and queued requests are answered `redo held`, and it gives up the slot. A `go` it has not pushed yet
    stays outstanding, as for `yield`.
    """
    record = _unit(state, unit)
    if "\n" in reason or "\r" in reason or not reason.strip():
        raise RunError("a hold reason must be a single non-empty line")
    if record["done"]:
        raise RunError(f"unit {unit!r} is done and cannot be held")
    _refuse_stopped(record, unit)
    if record["hold"] is not None:
        raise RunError(f"unit {unit!r} is already held: {record['hold']['reason']}")
    unanswered = [e["sha"] for key in ("pending", "queue") for e in state[key] if e["unit"] == unit]
    for sha in dict.fromkeys(unanswered):  # each sha once, in order
        _append(run_dir, PUSHES, f"{unit} redo {sha} held\n")
        _note_answered(run_dir, state, unit, sha)
    _drop_entries(state, unit, "pending", "queue")
    if _holds_slot(state, unit):
        state["slot"] = None
    record["hold"] = {"reason": reason, "at": now}


def release(state: dict, unit: str) -> None:
    """Lift a unit's hold; it is mergeable again, and the watcher reports it afresh.

    The stall clock restarts: the hold was waiting on purpose, so the watcher starts a new stall window. A READY
    or REMIND reported before the hold is forgotten, so the same head is reported READY again.
    """
    record = _unit(state, unit)
    if record["hold"] is None:
        raise RunError(f"unit {unit!r} is not held")
    record["hold"] = None
    marks = state["marks"].get(unit, {})
    for key in ("signature", "stalled_key", "ready_head", "ready_at", "remind_head"):
        marks.pop(key, None)


def record_merged(state: dict, unit: str, pr: int, *, more: bool) -> None:
    """Note a merged PR: free the slot if held, lift any hold, and mark the unit done unless more PRs are coming."""
    record = _unit(state, unit)
    _forget_freed(state, unit)
    record["prs"][str(pr)] = "MERGED"
    record["hold"] = None
    if _holds_slot(state, unit):
        state["slot"] = None
    record["done"] = not more


def record_closed(state: dict, unit: str, pr: int) -> None:
    """Note a PR closed without merging, lift any hold, and free the slot if the unit holds it."""
    record = _unit(state, unit)
    _forget_freed(state, unit)
    record["prs"][str(pr)] = "CLOSED"
    record["hold"] = None
    if _holds_slot(state, unit):
        state["slot"] = None


def sync_in_flight(state: dict) -> bool:
    """True when the integration unit has a request pending, queued or a `go` not yet recorded as synced."""
    name = integration_unit(state)
    if name is None:
        return False
    return any(entry["unit"] == name for key in ("pending", "queue") for entry in state[key]) or "go_sha" in state[
        "marks"
    ].get(name, {})


def integration_block(state: dict, unit: str) -> str | None:
    """Why the unit may not merge for the integration branch's sake, or None.

    Units wait while an integration sync is in flight; the integration unit waits for every other unit.
    """
    name = integration_unit(state)
    if name is None:
        return None
    if unit != name:
        return "integration sync in flight" if sync_in_flight(state) else None
    late = late_units(state)
    return f"waits on {', '.join(late)}" if late else None


def record_synced(state: dict, unit: str, sha: str) -> None:
    """Note that the integration unit pushed its approved `go` straight to the integration branch.

    The go is spent: it stops blocking unit merges, and the unit gives up the slot if it holds it.
    """
    record = _unit(state, unit)
    if not _is_integration(record):
        raise RunError("only the integration unit pushes directly")
    marks = state["marks"].get(unit, {})
    if marks.get("go_sha") != sha:
        raise RunError(f"no outstanding go for {sha[:7]}")
    marks.pop("go_sha")
    marks.pop("go_head", None)
    if _holds_slot(state, unit):
        state["slot"] = None


def reportable(state: dict, unit: str) -> bool:
    """True when the unit may be reported on (READY/REMIND): it is not stopped, it is mergeable, nothing blocks it
    for the integration branch, and the run is in parallel mode or the slot is free or its own."""
    if _unit(state, unit)["gate"] == "stopped" or not mergeable(state, unit) or integration_block(state, unit):
        return False
    return state["queue_mode"] == "parallel" or state["slot"] is None or state["slot"]["unit"] == unit
