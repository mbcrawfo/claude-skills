"""The run watcher: block until one event for the manager, print it as one line, and exit.

Events are push requests (new, or still unanswered), queue turns and PR changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Callable

from prstate import GhError, Summary, branch_head, find_open_pr, poll_pr
from runstate import (
    NEXT_MARK,
    REQUESTS,
    SHA_RE,
    SYNCING_MARK,
    UNIT_RE,
    integration_unit,
    load,
    locked,
    queue_head,
    reportable,
    wrong_base,
)

# The branch is printable non-space characters: no whitespace or control characters. `syncing` is the integration
# unit's notice that it is resolving a conflicted base merge before it asks to push.
REQUEST_RE = rf"^({UNIT_RE}) (push|syncing) ({SHA_RE}) ([^\s\x00-\x1f\x7f]+)$"

BAD_REQUESTS_KEPT = 20

# Consecutive PR poll rounds that failed on a gh call before `wait` reports ERROR.
GH_FAILURES_FOR_ERROR = 10

# PR event kinds in priority order; NEXT is checked between ORPHAN and BEHIND.
READY, REMIND, ENDED, SYNCED, ORPHAN, BEHIND, STUCK, STALLED = range(8)


def _note_bad(state: dict, line: str) -> None:
    state["bad_requests"] = (state["bad_requests"] + [line])[-BAD_REQUESTS_KEPT:]


def _forget_answers_read(state: dict, offset: int) -> None:
    """Drop the answers that cover only lines before `offset`, which no wait reads again."""
    for name, marks in state["marks"].items():
        answered = marks.get("answered") if name != NEXT_MARK else None
        if answered is None:
            continue
        for sha in [sha for sha, size in answered.items() if size <= offset]:
            del answered[sha]
        if not answered:
            del marks["answered"]


def take_request(run_dir: Path, state: dict, now: float) -> str | None:
    """Consume whole requests.log lines up to the first valid request and report it as `REQUEST <unit> <sha> <branch>`.

    Lines that are not a valid request for a known unit go to `bad_requests`; a trailing line without its
    newline is left for a later call. A request from a done unit is still reported. A request ends the unit's
    outstanding `go`: the child has moved on, and its new request is answered afresh. A request the manager
    answered (`go`, `redo` or `queue`) before any wait read it is consumed without a report.

    A `syncing` line from the integration unit is consumed without a report: it marks a sync in flight, which
    holds unit merges, until the unit's next push request (or a redo, stop, merge or close) ends it. From any
    other unit it is a bad line.
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
        for name, marks in state["marks"].items():
            if name != NEXT_MARK:
                marks.pop("answered", None)
    _forget_answers_read(state, offset)
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
        unit, kind, sha, branch = match.groups()
        if kind == "syncing":
            if unit != integration_unit(state):
                _note_bad(state, line)
            else:
                state["marks"].setdefault(unit, {})[SYNCING_MARK] = {"sha": sha, "at": now}
            continue
        state["marks"].get(unit, {}).pop(SYNCING_MARK, None)
        if state["marks"].get(unit, {}).get("answered", {}).get(sha, -1) >= offset:
            continue  # answered before this line was read
        state["pending"] = [entry for entry in state["pending"] if entry["unit"] != unit]
        state["pending"].append({"unit": unit, "sha": sha, "branch": branch, "at": now})
        state["marks"].get(unit, {}).pop("go_head", None)
        state["marks"].get(unit, {}).pop("go_sha", None)
        return f"REQUEST {unit} {sha} {branch}"
    return None


def pending_event(state: dict, now: float, pending_seconds: float) -> str | None:
    """Report a request still unanswered `pending_seconds` after its REQUEST as `PENDING <unit> <sha> <branch>`,
    once per request."""
    for entry in state["pending"]:
        key = f"{entry['sha']} {entry['at']}"
        if now - entry["at"] >= pending_seconds and state["marks"].get(entry["unit"], {}).get("pending_key") != key:
            state["marks"].setdefault(entry["unit"], {})["pending_key"] = key
            return f"PENDING {entry['unit']} {entry['sha']} {entry['branch']}"
    return None


def syncing_event(state: dict, now: float, syncing_seconds: float) -> str | None:
    """Report an announced sync with no push request `syncing_seconds` after it as `STUCK <unit> - syncing`, once
    per announcement: it holds every unit merge until it ends."""
    name = integration_unit(state)
    marks = state["marks"].get(name, {}) if name else {}
    syncing = marks.get(SYNCING_MARK)
    if syncing is None or now - syncing["at"] < syncing_seconds or syncing.get("reported"):
        return None
    syncing["reported"] = True
    return f"STUCK {name} - syncing"


def next_event(state: dict) -> str | None:
    """In serial mode with the slot free, report the head of the queue as `NEXT <unit> <sha>`, once per (unit, sha).

    Queue entries for done units are dropped first; entries for units that are not mergeable are skipped. While
    the slot is held the mark is cleared.
    """
    if state["queue_mode"] != "serial":
        return None
    if state["slot"] is not None:
        # Someone holds the slot, so the last NEXT was answered: the next free slot reports the head afresh.
        state["marks"].pop(NEXT_MARK, None)
        return None
    state["queue"] = [entry for entry in state["queue"] if not state["units"][entry["unit"]]["done"]]
    head = queue_head(state)
    if head is None:
        return None
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
    is recorded as `OPEN` unless the manager recorded it `MERGED`. The PR number and the head seen go to
    `marks[unit]["pr"]` and `["head"]`, and in the integration mode its base branch to `["base"]`.
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
        if record["prs"].get(str(pr)) != "MERGED":  # a merged PR never reopens; a closed one may
            record["prs"][str(pr)] = "OPEN"
        summary = poll_pr(state["repo"], pr, gh)
        marks["pr"] = pr
        marks["head"] = summary.head
        if state.get("integration"):
            marks["base"] = summary.base
        polled[unit] = summary
    return polled


def poll_orphans(state: dict, *, gh: Callable[[list[str]], str]) -> dict[str, int]:
    """Find an open PR on the branch of every done unit: one opened after the unit's last merge.

    Raises GhError on the first failing gh call.
    """
    orphans = {}
    for unit in _units_in_order(state):
        record = state["units"][unit]
        if record["branch"] and record["done"]:
            pr = find_open_pr(state["repo"], record["branch"], gh)
            if pr is not None:
                orphans[unit] = pr
    return orphans


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
    # The owner merges the final PR: it is reported as FINAL-READY, once per head and never reminded.
    owner_final = unit == integration_unit(state) and (state.get("integration") or {}).get("final_merge") == "owner"
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
    recorded = state["units"][unit]["prs"].get(key_pr)
    if summary.state != "OPEN" or recorded == "MERGED":
        if key_pr not in ended:
            if recorded == "OPEN":
                events.append((ENDED, f"{summary.state} {unit} {pr}", lambda: ended.append(key_pr)))
            else:
                ended.append(key_pr)  # the manager recorded it first
        return events
    if key_pr in ended:
        ended.remove(key_pr)  # reopened

    head = summary.head
    ready_key = f"{pr} {head}"
    reasons = summary.reasons()
    waiting = pending or any(entry["unit"] == unit for entry in state["queue"])
    if "go_head" in marks and marks["go_head"] != head:
        marks.pop("go_head")  # the push after the last go has landed
    # After a go, the old head is not READY: the child is about to push.
    pushing = "go_head" in marks
    may_report = not waiting and not pushing and reportable(state, unit) and not wrong_base(state, unit, summary.base)

    if reasons or waiting:
        # A request makes the unit's head stale, so a candidate seen meanwhile does not count afterwards.
        marks.pop("candidate", None)
    else:
        seen = asdict(summary)
        candidate = marks.get("candidate")
        if candidate is None or candidate["pr"] != pr or candidate["summary"] != seen:
            candidate = marks["candidate"] = {"pr": pr, "summary": seen, "at": now}
        confirmed = now - candidate["at"] >= timings.confirm_seconds
        if marks.get("ready_head") != ready_key:
            if confirmed and may_report:
                mark = _setter(marks, ready_head=ready_key, ready_at=now)
                events.append((READY, f"{'FINAL-READY' if owner_final else 'READY'} {unit} {pr} {head}", mark))
        elif (
            may_report
            and not owner_final
            and now - marks["ready_at"] >= timings.remind_seconds
            and marks.get("remind_head") != ready_key
        ):
            events.append((REMIND, f"REMIND {unit} {pr} {head}", _setter(marks, remind_head=ready_key)))

    if responsible and summary.merge == "BEHIND" and marks.get("behind_head") != head:
        events.append((BEHIND, f"BEHIND {unit} {pr} {head}", _setter(marks, behind_head=head)))

    if summary.failed or summary.unresolved or summary.conflict:
        since = marks.get("fail_since")
        if since is None or since["pr"] != pr or since["head"] != head:
            since = marks["fail_since"] = {"pr": pr, "head": head, "at": now}
        # A holder's failure counts only from when it took the slot. While its go is outstanding the fix is on
        # its way: the no-push STUCK covers a go that never lands.
        start = max(since["at"], slot["at"]) if serial and holder else since["at"]
        key = f"{pr} {head}{reasons}"
        quiet = pending or (serial and holder and pushing)
        if responsible and not quiet and now - start >= timings.stuck_seconds and marks.get("stuck_key") != key:
            events.append((STUCK, f"STUCK {unit} {pr}{reasons}", _setter(marks, stuck_key=key)))
    else:
        marks.pop("fail_since", None)
    if serial and holder:
        no_push(pr)

    signature = f"{pr} {head}{reasons}"
    seen_since = marks.get("signature")
    if seen_since is None or seen_since["key"] != signature:
        seen_since = marks["signature"] = {"key": signature, "at": now}
    # A hold waits on purpose, so it is not stalled; an `answer sent:` hold waits on the child, so it can be.
    hold = state["units"][unit]["hold"]
    held = hold is not None and not hold["reason"].startswith("answer sent:")
    if not held and now - seen_since["at"] >= timings.stall_seconds and marks.get("stalled_key") != signature:
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
    orphans: dict[str, int] | None = None,
    synced: dict[str, str] | None = None,
) -> str | None:
    """Apply one PR poll to the marks and return at most one event line, the first by kind and then unit order.

    Every polled unit's tracking marks are updated; only the reported event's mark is set. `orphans` (a done
    unit's open PR) are reported as `ORPHAN <unit> <pr>`, once per PR. `before_behind` (the queue's NEXT) is
    asked after READY, REMIND, MERGED/CLOSED, SYNCED and ORPHAN and before BEHIND, STUCK and STALLED.
    `synced` maps the integration unit to the branch head seen this poll; when that is the unit's outstanding
    `go_sha` it is reported as `SYNCED <unit> <sha>`, once per sha, with or without an open PR.
    While an ORPHAN waits to be reported, the final PR is not reported READY or REMIND: once reported, the
    orphan is late work the final PR waits on.
    """
    found: dict[int, list[tuple[str, Callable[[], None]]]] = {}
    final: list[tuple[int, tuple[str, Callable[[], None]]]] = []  # the final PR's READY or REMIND
    final_unit = integration_unit(state)
    for unit in _units_in_order(state):
        if unit in polled:
            for kind, line, mark in _unit_events(state, unit, polled[unit], now, timings):
                if kind in (READY, REMIND) and unit == final_unit:
                    final.append((kind, (line, mark)))
                else:
                    found.setdefault(kind, []).append((line, mark))
        head = (synced or {}).get(unit)
        marks = state["marks"].get(unit, {})
        if head is not None and marks.get("go_sha") == head and marks.get("synced_sha") != head:
            found.setdefault(SYNCED, []).append((f"SYNCED {unit} {head}", _setter(marks, synced_sha=head)))
        if unit in (orphans or {}):
            marks = state["marks"].setdefault(unit, {})
            reported = marks.setdefault("orphans", [])
            pr = orphans[unit]
            if pr not in reported:
                found.setdefault(ORPHAN, []).append((f"ORPHAN {unit} {pr}", lambda r=reported, p=pr: r.append(p)))
    if ORPHAN not in found:
        for kind, entry in final:
            found.setdefault(kind, []).append(entry)
    for kind in (READY, REMIND, ENDED, SYNCED, ORPHAN, None, BEHIND, STUCK, STALLED):
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


def poll_heads(state: dict, *, gh: Callable[[list[str]], str]) -> dict[str, str]:
    """The integration branch's head, keyed by the integration unit, when that unit has an outstanding `go_sha`.

    One call per poll; empty (and no call) in a run without the mode, with no go outstanding, or once the unit is
    done: its branch may be deleted after the final PR merges.
    Raises GhError on a failing gh call.
    """
    unit = integration_unit(state)
    if unit is None or "go_sha" not in state["marks"].get(unit, {}):
        return {}
    record = state["units"][unit]
    if not record["branch"] or record["done"]:
        return {}
    return {unit: branch_head(state["repo"], record["branch"], gh)}


def _poll_snapshot(run_dir: Path, gh: Callable[[list[str]], str]) -> tuple[dict, dict, dict, dict, dict]:
    """Poll the PRs on an unlocked snapshot; return it with each unit's identity before the poll, the polled
    units, the orphans and the integration branch head."""
    snapshot = load(run_dir)
    before = {name: _unit_identity(record) for name, record in snapshot["units"].items()}
    polled = poll_units(snapshot, gh=gh)
    return snapshot, before, polled, poll_orphans(snapshot, gh=gh), poll_heads(snapshot, gh=gh)


def _apply_poll(
    state: dict, snapshot: dict, before: dict, polled: dict, orphans: dict, heads: dict
) -> tuple[dict[str, Summary | None], dict[str, int], dict[str, str]]:
    """Carry the poll's records into the fresh state, dropping units whose branch, PRs or done changed meanwhile."""

    def unchanged(unit: str) -> bool:
        record = state["units"].get(unit)
        return record is not None and _unit_identity(record) == before[unit]

    # Decide before the loop below records the polled PRs, which would make the unit look changed.
    fresh = {unit for unit in before if unchanged(unit)}
    kept = {}
    for unit, summary in polled.items():
        record = state["units"].get(unit)
        if unit not in fresh:
            continue
        record["prs"] = snapshot["units"][unit]["prs"]
        polled_marks = snapshot["marks"][unit]
        marks = state["marks"].setdefault(unit, {})
        for key in ("pr", "head", "base"):
            if key in polled_marks:
                marks[key] = polled_marks[key]
        kept[unit] = summary
    return (
        kept,
        {unit: pr for unit, pr in orphans.items() if unit in fresh},
        {unit: head for unit, head in heads.items() if unit in fresh},
    )


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
    pending_seconds: float = 900.0,
    syncing_seconds: float = 3600.0,
) -> str:
    """Loop until there is one event for the manager and return its line.

    Each step takes the run lock on its own, so the lock is never held across a sleep or a gh call and
    state changes the manager makes between steps are kept. PRs are polled on the first pass, then every
    `poll_seconds`, or at a READY candidate's confirm time. After `GH_FAILURES_FOR_ERROR` failed poll rounds
    in a row it returns `ERROR <message>`; requests are still reported first. A request still unanswered
    `pending_seconds` after it was reported is reported again, once, as `PENDING`. An integration sync announced
    with `syncing` and not followed by a push request within `syncing_seconds` is reported, once, as
    `STUCK <unit> - syncing`.
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
            line = (
                take_request(run_dir, state, now())
                or pending_event(state, now(), pending_seconds)
                or syncing_event(state, now(), syncing_seconds)
            )
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
                kept, orphans, heads = _apply_poll(state, *poll)
                # A request that arrived during the gh round comes first, and its pending entry holds READY.
                line = take_request(run_dir, state, now())
                if not line:
                    line = pr_event(
                        state,
                        kept,
                        polled_at,
                        timings=timings,
                        before_behind=lambda: next_event(state),
                        orphans=orphans,
                        synced=heads,
                    )
                next_poll_at = _next_poll_at(state, polled_at, poll_seconds, confirm_seconds)
        if line:
            return line
        sleep(request_seconds)
