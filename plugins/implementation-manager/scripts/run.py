#!/usr/bin/env python3
"""The implementation manager's run script: one command line over the run folder and its watcher.

The run folder is always the first argument after the subcommand:

  init <dir> --repo O/R --base B --method merge|squash|rebase [--wip N] [--queue serial|parallel]
       [--integration BRANCH --final-merge manager|owner --feature-review yes|no]
  unit <dir> <name> [--prereq U ...] [--after U ...|none] [--branch B] [--worktree P] [--specs] [--title T]
       [--integration]              the run's one integration unit (needs init --integration)
  set <dir> wip <N|none> | set <dir> queue <serial|parallel>
  set <dir> final-merge <manager|owner> | set <dir> feature-review <yes|no>
                                    integration mode, until the final gate opens
                                    in parallel mode, print each request still queued: queued <unit> <sha>
  open <dir> <unit>                 open one gate
  open-ready <dir>                  open every gate that may open; print one unit name per line, then the
                                    final gate's gates.log line when it opens (integration mode)
  stop <dir> <unit> <reason...>     the unit never opens, gets go or queues again, and gives up the slot
  go <dir> <unit> <sha>             serial mode: take the push slot, or refuse while another unit holds it
  redo <dir> <unit> <sha> <reasons...>
  hold <dir> <unit> <reason...>     hold the unit's merge: no READY or REMIND, no go, not first in the queue
  release <dir> <unit>              lift the hold
  queue <dir> <unit> <sha>          line a covered request up behind the slot holder
  synced <dir> <unit> <sha>         the integration unit pushed its approved go to the integration branch
  yield <dir> <unit> <reason...>    the slot holder gives the slot up
  merged <dir> <unit> <pr> [--more] record a merge; print the gates that may open now, one per line
  closed <dir> <unit> <pr>
  status <dir>                      the tracker and everything waiting on the manager
  clear-bad <dir>                   forget the bad request lines status lists, once they are handled
  check <dir> <unit>                one readiness check of the unit's PR, printed as one line; in integration
                                    mode READY becomes NOT-READY ... blocked: <reason> or FINAL-READY
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


def _owner_merges(state: dict, unit: str) -> bool:
    """The unit is the integration unit and the owner, not the manager, merges its final PR."""
    block = state.get("integration")
    return bool(block) and block["final_merge"] == "owner" and unit == runstate.integration_unit(state)


def _label(state: dict, name: str) -> str:
    """The unit's name with its display title, for lines a person reads; commands keep the bare name."""
    title = state["units"][name]["title"]
    return f"{name} ({title})" if title else name


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
            needs.append(f"hold {_label(state, name)}: {hold['reason']} since {_clock(hold['at'])}")
        ready = marks.get("ready_head")
        owner_final = _owner_merges(state, name)
        if (
            ready
            and runstate.mergeable(state, name)  # not held, and every --after unit merged or stopped
            and not runstate.integration_block(state, name)
            and not owner_final
        ):
            pr, _, sha = ready.partition(" ")
            if record["prs"].get(pr) == "OPEN" and marks.get("head") == sha:
                needs.append(f"{_label(state, name)} PR #{pr} ready since {_clock(marks['ready_at'])}")
        if "go_head" in marks:
            sha = _last_go(run_dir, name)
            needs.append(f"push approved for {name} {sha[:7] if sha else '-'}, not seen yet")
        elif _behind_holder(state, name):
            needs.append(f"slot holder {name} PR #{marks['pr']} behind base")
        if (
            marks.get(runstate.FREED_MARK)
            and record["gate"] != "stopped"
            and runstate.mergeable(state, name)
            and "go_head" not in marks
            and not any(entry["unit"] == name for entry in state["pending"] + state["queue"])
        ):
            # It lost the slot when it gained --after, and nothing else makes its child ask again.
            needs.append(f"{name} can merge now its --after units merged: ask it to sync and push")
    if state["queue_mode"] == "serial" and state["slot"] is None:
        head = runstate.queue_head(state)  # the head the watcher reports as NEXT
        if head:
            needs.append(f"slot free with a queue, next {head['unit']} {head['sha']}")
    needs += [f"bad request line: {line}" for line in state["bad_requests"]]
    needs += [f"gate {name} may open" for name in runstate.openable(state)]
    if runstate.final_due(state):
        needs.append(f"final gate for {runstate.integration_unit(state)} may open: run open-ready")
    if runstate.all_stopped(state):
        needs.append(f"every unit stopped: ask the owner what to do with {state['integration']['branch']}")
    return needs


def _integration_lines(state: dict) -> list[str]:
    """The status lines of the integration mode; none in a default run."""
    block = state.get("integration")
    if not block:
        return []
    lines = [
        f"- integration: {block['branch']} into {state['base']}"
        f" · final merge: {block['final_merge']} · feature review: {'yes' if block['feature_review'] else 'no'}"
    ]
    if runstate.sync_in_flight(state):
        lines.append("- integration sync in flight")
    name = runstate.integration_unit(state)
    record = state["units"].get(name) if name else None
    open_prs = [pr for pr, status in record["prs"].items() if status == "OPEN"] if record else []
    if open_prs:
        pr = max(open_prs, key=int)
        late = runstate.late_units(state)
        if late:
            lines.append(f"- final PR #{pr} waits on {', '.join(late)}")
        marks = state["marks"].get(name, {})
        if _owner_merges(state, name) and marks.get("ready_head") == f"{pr} {marks.get('head')}":
            lines.append(f"- final PR #{pr} ready: the owner merges it")
    return lines


def _status(run_dir: Path, state: dict) -> str:
    units = state["units"]
    order = _units_in_order(state)
    wip = "none" if state["wip"] is None else str(state["wip"])
    lines = [f"Run {run_dir.resolve().name} ({state['repo']}), queue {state['queue_mode']}, WIP {wip}"]
    slot = state["slot"]
    lines.append(f"- slot: {slot['unit']} {slot['sha'][:7]} since {_clock(slot['at'])}" if slot else "- slot: free")
    queue = [f"{entry['unit']} {entry['sha'][:7]}" for entry in runstate.queue_order(state)]
    lines.append(f"- queue: {', '.join(queue)}" if queue else "- queue: empty")
    merged = [
        f"{_label(state, name)} #{pr}"
        for name in order
        for pr, status in units[name]["prs"].items()
        if status == "MERGED"
    ]
    lines.append(f"- merged: {', '.join(merged) or 'none'}")
    open_units = []
    gated = []
    openable = runstate.openable(state)
    for name in order:
        record = units[name]
        if record["done"]:
            continue
        if record["gate"] == "stopped":
            gated.append(f"{_label(state, name)} stopped")
        elif record["gate"] == "open":
            prs = [pr for pr, status in record["prs"].items() if status == "OPEN"]
            label = _label(state, name)
            entry = f"{label} PR #{max(prs, key=int)}" if prs else f"{label} working"
            unmerged = [u for u in record["after"] if not units[u]["done"] and units[u]["gate"] != "stopped"]
            if unmerged:
                entry += f", merges after {', '.join(_label(state, u) for u in unmerged)}"
            open_units.append(entry)
        else:
            waiting_on = [p for p in record["prereqs"] if not units[p]["done"]]
            if waiting_on:
                gated.append(f"{_label(state, name)} on {', '.join(_label(state, p) for p in waiting_on)}")
            else:
                label = _label(state, name)
                gated.append(f"{label} may open" if name in openable else f"{label} on WIP")
    lines.append(f"- open: {', '.join(open_units) or 'none'}")
    lines.append(f"- gated: {', '.join(gated) or 'none'}")
    lines += _integration_lines(state)
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
    p.add_argument("--integration", metavar="BRANCH", help="integration mode: units merge into this branch")
    p.add_argument("--final-merge", choices=runstate.FINAL_MERGES, help="who merges the final PR (integration mode)")
    p.add_argument("--feature-review", choices=("yes", "no"), help="review the whole feature (integration mode)")

    p = command("unit", "add a unit, or update the fields given")
    p.add_argument("name")
    p.add_argument("--prereq", nargs="+", action="extend", metavar="U")
    p.add_argument("--after", nargs="+", action="extend", metavar="U", help="units that merge first; none clears")
    p.add_argument("--branch")
    p.add_argument("--worktree")
    p.add_argument("--specs", action="store_true", default=None, help="the manager's own planning PR")
    p.add_argument("--title", help="one-line display label shown beside the name in status")
    p.add_argument("--integration", action="store_true", default=None, help="the run's one integration unit")

    p = command("set", "change the WIP limit, the queue mode, or an integration choice")
    p.add_argument("key", choices=("wip", "queue", "final-merge", "feature-review"))
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
    p = command("synced", "record the integration unit's push to the integration branch")
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


def _integration_choice(args: argparse.Namespace) -> dict | None:
    """The init options of the integration mode as init_run takes them; all three or none."""
    given = (args.integration, args.final_merge, args.feature_review)
    if all(value is None for value in given):
        return None
    if any(value is None for value in given):
        raise RunError("--integration, --final-merge and --feature-review go together: give all three or none")
    return {
        "branch": args.integration,
        "final_merge": args.final_merge,
        "feature_review": args.feature_review == "yes",
    }


def _integration_verdict(state: dict, unit: str, line: str) -> str:
    """Turn a READY line into NOT-READY when the integration branch blocks the unit, or FINAL-READY for the owner."""
    verb, _, rest = line.partition(" ")
    if verb != "READY":
        return line
    reason = runstate.integration_block(state, unit)
    if reason:
        pr, _, sha = rest.partition(" ")
        return f"NOT-READY {pr} head={sha[:7]} blocked: {reason}"
    return f"FINAL-READY {rest}" if _owner_merges(state, unit) else line


def _run(args: argparse.Namespace) -> str | None:
    """Run one command other than wait; return what it prints, if anything."""
    run_dir: Path = args.dir
    now = time.time()
    command = args.command
    if command == "init":
        runstate.init_run(
            run_dir,
            repo=args.repo,
            base=args.base,
            method=args.method,
            wip=args.wip,
            queue_mode=args.queue,
            now=now,
            integration=_integration_choice(args),
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
        line = check_once(state["repo"], record["branch"], gh=run_gh, sleep=time.sleep)
        return _integration_verdict(state, args.unit, line)
    with locked(run_dir) as state:
        if command == "unit":
            if args.after and "none" in args.after and len(args.after) > 1:
                raise RunError("--after none clears the list; give it alone")
            after = [] if args.after == ["none"] else args.after
            runstate.add_unit(
                state,
                args.name,
                prereqs=args.prereq,
                after=after,
                branch=args.branch,
                worktree=args.worktree,
                specs=args.specs,
                title=args.title,
                integration=args.integration,
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
            if runstate.final_due(state):
                names.append(runstate.open_final(run_dir, state))
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
        elif command == "synced":
            runstate.record_synced(state, args.unit, args.sha)
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
