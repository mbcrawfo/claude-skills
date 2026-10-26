#!/usr/bin/env python3
"""The implementation manager's run script: one command line over the run folder and its watcher.

The run folder is always the first argument after the subcommand:

  init <dir> --repo O/R --base B --method merge|squash|rebase [--wip N] [--queue serial|parallel]
  unit <dir> <name> [--prereq U ...] [--after U ...|none] [--branch B] [--worktree P] [--specs]
  set <dir> wip <N|none> | set <dir> queue <serial|parallel>
                                    in parallel mode, print each request still queued: queued <unit> <sha>
  open <dir> <unit>                 open one gate
  open-ready <dir>                  open every gate that may open; print one unit name per line
  stop <dir> <unit> <reason...>     the unit never opens, gets go or queues again, and gives up the slot
  go <dir> <unit> <sha>             serial mode: take the push slot, or refuse while another unit holds it
  redo <dir> <unit> <sha> <reasons...>
  hold <dir> <unit> <reason...>     hold the unit's merge: no READY, REMIND or STALLED, no go, not first in the queue
  release <dir> <unit>              lift the hold
  queue <dir> <unit> <sha>          line a covered request up behind the slot holder
  yield <dir> <unit> <reason...>    the slot holder gives the slot up
  merged <dir> <unit> <pr> [--more] record a merge; print the gates that may open now, one per line
  closed <dir> <unit> <pr>
  status <dir>                      the tracker and everything waiting on the manager
  clear-bad <dir>                   forget the bad request lines status lists, once they are handled
  check <dir> <unit>                one readiness check of the unit's PR, printed as one line
  wait <dir> [--stall-hours N]      block until one event, print it as one line, exit 0 (also on error)

Exit codes: 0 on success, 1 for a refused command (one line on stderr), 2 for a usage error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from prstate import GhError, check_once, run_gh
import runstate
from runstate import PUSHES, RunError, load, locked
import watcher


def _clock(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _units_in_order(state: dict) -> list[str]:
    return sorted(state["units"], key=lambda name: state["units"][name]["order"])


def _last_go(run_dir: Path, unit: str) -> str | None:
    """The sha of the unit's last `go` line in pushes.log."""
    sha = None
    pattern = re.compile(rf"^{re.escape(unit)} go ([0-9a-f]{{40}})$")
    with open(run_dir / PUSHES, encoding="utf-8", errors="replace") as f:
        for line in f:
            match = pattern.match(line.rstrip("\n"))
            if match:
                sha = match.group(1)
    return sha


def _behind_holder(state: dict, unit: str) -> bool:
    """The slot holder's open PR was BEHIND at its current head, and the unit has no request or push under way.

    A lost BEHIND event leaves exactly this; the child syncs once it is told to.
    """
    slot = state["slot"]
    marks = state["marks"].get(unit, {})
    behind = marks.get("behind_head")
    return (
        slot is not None
        and slot["unit"] == unit
        and behind is not None
        and behind == marks.get("head")
        and state["units"][unit]["prs"].get(str(marks.get("pr"))) == "OPEN"
        and "go_head" not in marks
        and not any(entry["unit"] == unit for entry in state["pending"])
    )


def _needs(run_dir: Path, state: dict) -> list[str]:
    """What waits on the manager, one line each."""
    units = state["units"]
    needs = [
        f"request {entry['unit']} {entry['sha']} {entry['branch']} unanswered since {_clock(entry['at'])}"
        for entry in state["pending"]
    ]
    try:
        unread = (run_dir / runstate.REQUESTS).stat().st_size > state["requests_offset"]
    except FileNotFoundError:
        unread = False
    if unread:
        needs.append("requests.log has lines no wait has read yet: start a wait")
    for name in _units_in_order(state):
        record = units[name]
        marks = state["marks"].get(name, {})
        if record["done"]:
            continue
        hold = record["hold"]
        if hold:
            needs.append(f"hold {name}: {hold['reason']} since {_clock(hold['at'])}")
        ready = marks.get("ready_head")
        if ready and not hold:
            pr, _, sha = ready.partition(" ")
            if record["prs"].get(pr) == "OPEN" and marks.get("head") == sha:
                needs.append(f"{name} PR #{pr} ready since {_clock(marks['ready_at'])}")
        if "go_head" in marks:
            sha = _last_go(run_dir, name)
            needs.append(f"push approved for {name} {sha[:7] if sha else '-'}, not seen yet")
        elif _behind_holder(state, name):
            needs.append(f"slot holder {name} PR #{marks['pr']} behind base")
    if state["queue_mode"] == "serial" and state["slot"] is None:
        # The head the watcher reports as NEXT: not done, and mergeable.
        head = next(
            (
                e
                for e in runstate.queue_order(state)
                if not units[e["unit"]]["done"] and runstate.mergeable(state, e["unit"])
            ),
            None,
        )
        if head:
            needs.append(f"slot free with a queue, next {head['unit']} {head['sha']}")
    needs += [f"bad request line: {line}" for line in state["bad_requests"]]
    needs += [f"gate {name} may open" for name in runstate.openable(state)]
    return needs


def _status(run_dir: Path, state: dict) -> str:
    units = state["units"]
    order = _units_in_order(state)
    wip = "none" if state["wip"] is None else str(state["wip"])
    lines = [f"Run {run_dir.resolve().name} ({state['repo']}), queue {state['queue_mode']}, WIP {wip}"]
    slot = state["slot"]
    lines.append(f"- slot: {slot['unit']} {slot['sha'][:7]} since {_clock(slot['at'])}" if slot else "- slot: free")
    queue = [f"{entry['unit']} {entry['sha'][:7]}" for entry in runstate.queue_order(state)]
    lines.append(f"- queue: {', '.join(queue)}" if queue else "- queue: empty")
    merged = [f"{name} #{pr}" for name in order for pr, status in units[name]["prs"].items() if status == "MERGED"]
    lines.append(f"- merged: {', '.join(merged) or 'none'}")
    open_units = []
    gated = []
    openable = runstate.openable(state)
    for name in order:
        record = units[name]
        if record["done"]:
            continue
        if record["gate"] == "stopped":
            gated.append(f"{name} stopped")
        elif record["gate"] == "open":
            prs = [pr for pr, status in record["prs"].items() if status == "OPEN"]
            entry = f"{name} PR #{max(prs, key=int)}" if prs else f"{name} working"
            unmerged = [u for u in record["after"] if not units[u]["done"] and units[u]["gate"] != "stopped"]
            if unmerged:
                entry += f", merges after {', '.join(unmerged)}"
            open_units.append(entry)
        else:
            waiting_on = [p for p in record["prereqs"] if not units[p]["done"]]
            if waiting_on:
                gated.append(f"{name} on {', '.join(waiting_on)}")
            else:
                gated.append(f"{name} may open" if name in openable else f"{name} on WIP")
    lines.append(f"- open: {', '.join(open_units) or 'none'}")
    lines.append(f"- gated: {', '.join(gated) or 'none'}")
    needs = _needs(run_dir, state)
    if needs:
        lines.append("- needs the manager:")
        lines += [f"  - {line}" for line in needs]
    else:
        lines.append("- needs the manager: nothing")
    return "\n".join(lines)


def _wip(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {value!r}") from None


def _hours(value: str) -> float:
    try:
        hours = float(value)
    except ValueError:
        hours = 0
    if not hours > 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of hours: {value!r}")
    return hours


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="The implementation manager's run folder and watcher.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def command(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument("dir", type=Path, help="the run folder")
        return p

    p = command("init", "create the run folder")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--method", required=True, choices=runstate.METHODS)
    p.add_argument("--wip", type=_wip)
    p.add_argument("--queue", choices=runstate.QUEUE_MODES, default="serial")

    p = command("unit", "add a unit, or update the fields given")
    p.add_argument("name")
    p.add_argument("--prereq", nargs="+", action="extend", metavar="U")
    p.add_argument("--after", nargs="+", action="extend", metavar="U", help="units that merge first; none clears")
    p.add_argument("--branch")
    p.add_argument("--worktree")
    p.add_argument("--specs", action="store_true", default=None, help="the manager's own planning PR")

    p = command("set", "change the WIP limit or the queue mode")
    p.add_argument("key", choices=("wip", "queue"))
    p.add_argument("value")

    p = command("open", "open one unit's gate")
    p.add_argument("unit")
    command("open-ready", "open every gate that may open now")
    p = command("stop", "tell a unit to stop")
    p.add_argument("unit")
    p.add_argument("reason", nargs="+")

    for name, help in (("go", "allow a push"), ("queue", "hold a covered request until the slot frees")):
        p = command(name, help)
        p.add_argument("unit")
        p.add_argument("sha")
    p = command("redo", "refuse a push, with the reasons")
    p.add_argument("unit")
    p.add_argument("sha")
    p.add_argument("reasons", nargs="+")
    p = command("hold", "hold a unit's merge")
    p.add_argument("unit")
    p.add_argument("reason", nargs="+")
    p = command("release", "lift a unit's merge hold")
    p.add_argument("unit")
    p = command("yield", "take the push slot from its holder")
    p.add_argument("unit")
    p.add_argument("reason", nargs="+")

    p = command("merged", "record a merged PR")
    p.add_argument("unit")
    p.add_argument("pr", type=int)
    p.add_argument("--more", action="store_true", help="more parts follow; the unit is not done")
    p = command("closed", "record a PR closed without merging")
    p.add_argument("unit")
    p.add_argument("pr", type=int)

    command("status", "print the tracker and what waits on the manager")
    command("clear-bad", "forget the bad request lines status lists")
    p = command("check", "check the unit's PR once")
    p.add_argument("unit")
    p = command("wait", "block until one event and print it")
    p.add_argument("--stall-hours", type=_hours, default=3.0)
    return parser


def _wait(args: argparse.Namespace) -> int:
    try:
        line = watcher.wait(
            args.dir,
            gh=run_gh,
            sleep=time.sleep,
            now=time.time,
            stall_seconds=args.stall_hours * 3600,
        )
    except Exception as exc:  # a background wait must always end with one line to read
        line = f"ERROR {_describe(args.dir, exc)}"
    print(" ".join(line.split()), flush=True)
    return 0


def _describe(run_dir: Path, exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return f"{run_dir / runstate.STATE_FILE} is not valid JSON: {exc}"
    if isinstance(exc, (RunError, GhError)):
        return str(exc)
    if isinstance(exc, OSError):
        return f"cannot read the run folder {run_dir}: {exc.strerror or exc}"
    return f"{run_dir / runstate.STATE_FILE} is not a run state ({type(exc).__name__}: {exc})"


def _run(args: argparse.Namespace) -> str | None:
    """Run one command other than wait; return what it prints, if anything."""
    run_dir: Path = args.dir
    now = time.time()
    command = args.command
    if command == "init":
        runstate.init_run(
            run_dir, repo=args.repo, base=args.base, method=args.method, wip=args.wip, queue_mode=args.queue, now=now
        )
        return None
    if command == "status":
        return _status(run_dir, load(run_dir))
    if command == "check":
        state = load(run_dir)
        record = state["units"].get(args.unit)
        if record is None:
            raise RunError(f"unknown unit {args.unit!r}")
        if not record["branch"]:
            raise RunError(f"unit {args.unit!r} has no branch yet; record it with: unit <dir> {args.unit} --branch B")
        return check_once(state["repo"], record["branch"], gh=run_gh, sleep=time.sleep)
    with locked(run_dir) as state:
        if command == "unit":
            after = [] if args.after == ["none"] else args.after
            runstate.add_unit(
                state,
                args.name,
                prereqs=args.prereq,
                after=after,
                branch=args.branch,
                worktree=args.worktree,
                specs=args.specs,
            )
        elif command == "set":
            runstate.set_option(state, args.key, args.value)
            if state["queue_mode"] == "parallel":
                # Parallel mode has no queue turns: each request still queued needs its own go.
                queued = [f"queued {entry['unit']} {entry['sha']}" for entry in runstate.queue_order(state)]
                return "\n".join(queued) or None
        elif command == "open":
            runstate.open_gate(run_dir, state, args.unit, now)
        elif command == "open-ready":
            names = runstate.openable(state)
            for name in names:
                runstate.open_gate(run_dir, state, name, now)
            return "\n".join(names) or None
        elif command == "stop":
            runstate.stop_gate(run_dir, state, args.unit, " ".join(args.reason))
        elif command == "go":
            runstate.go(run_dir, state, args.unit, args.sha, now)
        elif command == "redo":
            runstate.redo(run_dir, state, args.unit, args.sha, " ".join(args.reasons))
        elif command == "hold":
            runstate.hold(run_dir, state, args.unit, " ".join(args.reason), now)
        elif command == "release":
            runstate.release(state, args.unit)
        elif command == "queue":
            runstate.enqueue(run_dir, state, args.unit, args.sha, now)
        elif command == "yield":
            runstate.yield_slot(state, args.unit, " ".join(args.reason), now)
        elif command == "merged":
            runstate.record_merged(state, args.unit, args.pr, more=args.more)
            return "\n".join(runstate.openable(state)) or None
        elif command == "closed":
            runstate.record_closed(state, args.unit, args.pr)
        elif command == "clear-bad":
            count = len(state["bad_requests"])
            state["bad_requests"] = []
            return f"cleared {count} bad request lines"
    return None


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "wait":
        return _wait(args)
    try:
        out = _run(args)
    except (RunError, GhError, OSError, json.JSONDecodeError) as exc:
        print(f"run.py {args.command}: {' '.join(_describe(args.dir, exc).split())}", file=sys.stderr)
        return 1
    if out:
        print(out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
