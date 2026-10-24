"""The run watcher: block until one event for the manager, print it as one line, and exit.

Events are push requests, queue turns and PR changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Callable

from prstate import GhError, Summary, find_open_pr, poll_pr
from runstate import NEXT_MARK, REQUESTS, SHA_RE, UNIT_RE, load, locked, queue_order, reportable

# The branch is printable non-space characters: no whitespace or control characters.
REQUEST_RE = rf"^({UNIT_RE}) push ({SHA_RE}) ([^\s\x00-\x1f\x7f]+)$"

BAD_REQUESTS_KEPT = 20

# Consecutive PR poll rounds that failed on a gh call before `wait` reports ERROR.
GH_FAILURES_FOR_ERROR = 10

# PR event kinds in priority order; NEXT is checked between ENDED and BEHIND.
READY, REMIND, ENDED, BEHIND, STUCK, STALLED = range(6)


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
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            _note_bad(state, raw.decode("utf-8", errors="replace"))
            continue
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

    Queue entries for done units are dropped first. While the slot is held the mark is cleared.
    """
    if state["queue_mode"] != "serial":
        return None
    if state["slot"] is not None:
        # Someone holds the slot, so the last NEXT was answered: the next free slot reports the head afresh.
        state["marks"].pop(NEXT_MARK, None)
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


@dataclass(frozen=True)
class Timings:
    """How long each PR condition must hold before `pr_event` reports it, in seconds."""

    confirm_seconds: float = 30.0
    remind_seconds: float = 1800.0
    stuck_seconds: float = 1200.0
    no_push_seconds: float = 1800.0
    stall_seconds: float = 10800.0


def _units_in_order(state: dict) -> list[str]:
    return sorted(state["units"], key=lambda name: state["units"][name]["order"])


def poll_units(state: dict, *, gh: Callable[[list[str]], str]) -> dict[str, Summary | None]:
    """Poll the PR of every unit with a branch that is not done; a unit maps to None when it has no open PR.

    The unit's PR is its recorded `OPEN` PR not yet reported ended; failing that, the branch's open PR, which
    is recorded as `OPEN`. The PR number and the head seen go to `marks[unit]["pr"]` and `["head"]`.
    Raises GhError on the first failing gh call.
    """
    polled: dict[str, Summary | None] = {}
    for unit in _units_in_order(state):
        record = state["units"][unit]
        if not record["branch"] or record["done"]:
            continue
        marks = state["marks"].setdefault(unit, {})
        ended = marks.get("ended", [])
        recorded = [int(pr) for pr, status in record["prs"].items() if status == "OPEN" and pr not in ended]
        pr = max(recorded) if recorded else find_open_pr(state["repo"], record["branch"], gh)
        if pr is None:
            polled[unit] = None
            continue
        record["prs"][str(pr)] = "OPEN"
        summary = poll_pr(state["repo"], pr, gh)
        marks["pr"] = pr
        marks["head"] = summary.head
        polled[unit] = summary
    return polled


def _setter(marks: dict, **values: object) -> Callable[[], None]:
    return lambda: marks.update(values)


def _unit_events(
    state: dict, unit: str, summary: Summary | None, now: float, timings: Timings
) -> list[tuple[int, str, Callable[[], None]]]:
    """Update the unit's tracking marks for this poll and list the events it could report, with their marks."""
    marks = state["marks"].setdefault(unit, {})
    slot = state["slot"]
    serial = state["queue_mode"] == "serial"
    holder = slot is not None and slot["unit"] == unit
    # BEHIND and STUCK are for the slot holder only, or for every open PR in parallel mode.
    responsible = holder or not serial
    pending = any(entry["unit"] == unit for entry in state["pending"])
    events: list[tuple[int, str, Callable[[], None]]] = []

    def no_push(pr: object) -> None:
        stale = summary is None or summary.head == slot["head"]
        if stale and now - slot["at"] >= timings.no_push_seconds and marks.get("no_push_at") != slot["at"]:
            events.append((STUCK, f"STUCK {unit} {pr} no-push", _setter(marks, no_push_at=slot["at"])))

    if summary is None or summary.state != "OPEN":
        for key in ("candidate", "fail_since", "signature"):
            marks.pop(key, None)
    if summary is None:
        if serial and holder:
            no_push("-")
        return events

    pr = marks["pr"]
    key_pr = str(pr)
    ended = marks.setdefault("ended", [])
    if summary.state != "OPEN":
        if key_pr not in ended:
            if state["units"][unit]["prs"].get(key_pr) == "OPEN":
                events.append((ENDED, f"{summary.state} {unit} {pr}", lambda: ended.append(key_pr)))
            else:
                ended.append(key_pr)  # the manager recorded it first
        return events
    if key_pr in ended:
        ended.remove(key_pr)  # reopened

    head = summary.head
    reasons = summary.reasons()
    waiting = pending or any(entry["unit"] == unit for entry in state["queue"])
    may_report = not waiting and reportable(state, unit)

    if reasons:
        marks.pop("candidate", None)
    else:
        seen = asdict(summary)
        candidate = marks.get("candidate")
        if candidate is None or candidate["pr"] != pr or candidate["summary"] != seen:
            candidate = marks["candidate"] = {"pr": pr, "summary": seen, "at": now}
        confirmed = now - candidate["at"] >= timings.confirm_seconds
        if marks.get("ready_head") != head:
            if confirmed and may_report:
                events.append((READY, f"READY {unit} {pr} {head}", _setter(marks, ready_head=head, ready_at=now)))
        elif may_report and now - marks["ready_at"] >= timings.remind_seconds and marks.get("remind_head") != head:
            events.append((REMIND, f"REMIND {unit} {pr} {head}", _setter(marks, remind_head=head)))

    if responsible and summary.merge == "BEHIND" and marks.get("behind_head") != head:
        events.append((BEHIND, f"BEHIND {unit} {pr} {head}", _setter(marks, behind_head=head)))

    if summary.failed or summary.unresolved or summary.conflict:
        since = marks.get("fail_since")
        if since is None or since["pr"] != pr or since["head"] != head:
            since = marks["fail_since"] = {"pr": pr, "head": head, "at": now}
        # A holder's failure counts only from when it took the slot.
        start = max(since["at"], slot["at"]) if serial and holder else since["at"]
        key = f"{pr} {head}{reasons}"
        if responsible and not pending and now - start >= timings.stuck_seconds and marks.get("stuck_key") != key:
            events.append((STUCK, f"STUCK {unit} {pr}{reasons}", _setter(marks, stuck_key=key)))
    else:
        marks.pop("fail_since", None)
    if serial and holder:
        no_push(pr)

    signature = f"{pr} {head}{reasons}"
    seen_since = marks.get("signature")
    if seen_since is None or seen_since["key"] != signature:
        seen_since = marks["signature"] = {"key": signature, "at": now}
    if now - seen_since["at"] >= timings.stall_seconds and marks.get("stalled_key") != signature:
        line = f"STALLED {unit} {pr} head={head[:7]}{reasons}"
        events.append((STALLED, line, _setter(marks, stalled_key=signature)))
    return events


def pr_event(
    state: dict,
    polled: dict[str, Summary | None],
    now: float,
    *,
    timings: Timings,
    before_behind: Callable[[], str | None] | None = None,
) -> str | None:
    """Apply one PR poll to the marks and return at most one event line, the first by kind and then unit order.

    Every polled unit's tracking marks are updated; only the reported event's mark is set. `before_behind`
    (the queue's NEXT) is asked after READY, REMIND and MERGED/CLOSED and before BEHIND, STUCK and STALLED.
    """
    found: dict[int, list[tuple[str, Callable[[], None]]]] = {}
    for unit in _units_in_order(state):
        if unit in polled:
            for kind, line, mark in _unit_events(state, unit, polled[unit], now, timings):
                found.setdefault(kind, []).append((line, mark))
    for kind in (READY, REMIND, ENDED, None, BEHIND, STUCK, STALLED):
        if kind is None:
            line = before_behind() if before_behind else None
            if line:
                return line
        elif kind in found:
            line, mark = found[kind][0]
            mark()
            return line
    return None


def _unit_identity(record: dict) -> tuple:
    return record["branch"], dict(record["prs"]), record["done"]


def _poll_snapshot(run_dir: Path, gh: Callable[[list[str]], str]) -> tuple[dict, dict, dict]:
    """Poll the PRs on an unlocked snapshot; return it with each unit's identity before the poll and the results."""
    snapshot = load(run_dir)
    before = {name: _unit_identity(record) for name, record in snapshot["units"].items()}
    return snapshot, before, poll_units(snapshot, gh=gh)


def _apply_poll(state: dict, snapshot: dict, before: dict, polled: dict) -> dict[str, Summary | None]:
    """Carry the poll's records into the fresh state, dropping units whose branch, PRs or done changed meanwhile."""
    kept = {}
    for unit, summary in polled.items():
        record = state["units"].get(unit)
        if record is None or _unit_identity(record) != before[unit]:
            continue
        record["prs"] = snapshot["units"][unit]["prs"]
        polled_marks = snapshot["marks"][unit]
        marks = state["marks"].setdefault(unit, {})
        for key in ("pr", "head"):
            if key in polled_marks:
                marks[key] = polled_marks[key]
        kept[unit] = summary
    return kept


def _next_poll_at(state: dict, polled_at: float, poll_seconds: float, confirm_seconds: float) -> float:
    """The next PR poll: `poll_seconds` on, or sooner when a READY candidate's confirm time comes first."""
    due = polled_at + poll_seconds
    for name, marks in state["marks"].items():
        candidate = marks.get("candidate") if name != NEXT_MARK else None
        if candidate and candidate["at"] + confirm_seconds > polled_at:
            due = min(due, candidate["at"] + confirm_seconds)
    return due


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
    state changes the manager makes between steps are kept. PRs are polled on the first pass, then every
    `poll_seconds`, or at a READY candidate's confirm time. After `GH_FAILURES_FOR_ERROR` failed poll rounds
    in a row it returns `ERROR <message>`; requests are still reported first.
    """
    timings = Timings(
        confirm_seconds=confirm_seconds,
        remind_seconds=remind_seconds,
        stuck_seconds=stuck_seconds,
        no_push_seconds=no_push_seconds,
        stall_seconds=stall_seconds,
    )
    next_poll_at: float | None = None
    failures = 0
    while True:
        with locked(run_dir) as state:
            line = take_request(run_dir, state, now())
        if line:
            return line
        poll = None
        if next_poll_at is None or now() >= next_poll_at:
            polled_at = now()
            try:
                poll = _poll_snapshot(run_dir, gh)
            except GhError as exc:
                failures += 1
                next_poll_at = polled_at + poll_seconds
                if failures >= GH_FAILURES_FOR_ERROR:
                    message = " ".join(str(exc).split())
                    return f"ERROR gh failed {failures} PR polls in a row: {message}"
            else:
                failures = 0
        with locked(run_dir) as state:
            if poll is None:
                line = next_event(state)
            else:
                kept = _apply_poll(state, *poll)
                line = pr_event(state, kept, polled_at, timings=timings, before_behind=lambda: next_event(state))
                next_poll_at = _next_poll_at(state, polled_at, poll_seconds, confirm_seconds)
        if line:
            return line
        sleep(request_seconds)
