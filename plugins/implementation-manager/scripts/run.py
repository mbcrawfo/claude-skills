#!/usr/bin/env python3
"""The implementation manager's run script: one command line over the run folder and its watcher.

The run folder is always the first argument after the subcommand:

  init <dir> --repo O/R --base B --method merge|squash|rebase [--wip N] [--queue serial|parallel]
       [--integration BRANCH --final-merge manager|owner --feature-review yes|no]   (not with rebase)
  unit <dir> <name> [--prereq U ...] [--after U ...|none] [--branch B] [--worktree P] [--specs] [--title T]
       [--integration]              the run's one integration unit (needs init --integration): its branch is the
                                    integration branch; no --prereq or --after, and no unit may name it in either;
                                    open-ready opens its gate at once, outside the WIP limit
                                    names use letters, digits, '-' and '_', starting with a letter or digit;
                                    each --prereq and --after unit must already exist; --prereq gates the start,
                                    --after only the merge; an --after unit that has neither merged nor stopped,
                                    given to the slot holder, frees the slot; a --worktree other than the
                                    stored one (compared normalized) starts a new session: the unit's open
                                    owner items from earlier sessions are dropped (print dropped <id> per item)
  set <dir> wip <N|none> | set <dir> queue <serial|parallel>
                                    in parallel mode, print each request still queued: queued <unit> <sha>
  set <dir> final-merge <manager|owner> | set <dir> feature-review <yes|no>
                                    integration mode, until the final gate opens
  open <dir> <unit>                 open one gate
  open-ready <dir>                  open every gate that may open; print one unit name per line, then the
                                    final gate's gates.log line when it opens (integration mode)
  stop <dir> <unit> <reason...>     the unit never opens, gets go or queues again, and gives up the slot; its owner
                                    items are dropped (print dropped <id> per item)
  go <dir> <unit> <sha>             serial mode: take the push slot, or refuse while another unit holds it, while
                                    the slot is free and another unit heads the queue, or while an --after unit
                                    is unmerged and not stopped; refuses a held unit in either mode
  redo <dir> <unit> <sha> <reasons...>
  hold <dir> <unit> <reason...>     hold the unit's merge: no READY or REMIND, no go, not first in the queue;
                                    frees the slot, drops the queue entry, answers waiting requests redo <sha> held;
                                    no STALLED unless the reason starts with "answer sent:"
  release <dir> <unit>              lift the hold, restart the stall clock, report a ready PR READY again
  queue <dir> <unit> <sha>          line a covered request up behind the slot holder; refuses a held unit, and
                                    the holder unless an --after unit blocks it (it then gives up the slot)
  synced <dir> <unit> <sha>         the integration unit pushed its approved go to the integration branch
  yield <dir> <unit> <reason...>    the slot holder gives the slot up
  merged <dir> <unit> <pr> [--more] record a merge and free the slot; print the gates that may open now, one per
                                    line; --more keeps the unit open for its next part
  closed <dir> <unit> <pr>          record a PR closed without merging and free the slot
  item add <dir> [unit] [--run-wide] [--ref Q<n>] --kind question|finding|command|decision --blocks yes|no
       [--depends Q<n> ...] --summary S --body-file P
                                    file an item for the owner (exactly one of unit and --run-wide; a run-wide
                                    item is a decision); print current <header>, queued <id> for <unit>, <N> waiting,
                                    or parked <id> with <parent>
  item current <dir>                the current item's header and body, or none
  item done <dir> <id> [--answer-file P]
                                    close an item; print the next current header (or none), then forward due lines
  item park <dir> <id> <note...> [--with ID ...]
                                    set the item, its dependents and the --with items aside; print each
                                    parked <id>, the next current header (or none), then forward due lines
  item unpark <dir> <id> [--queue]  make a parked item current (the displaced one is resumed next); --queue
                                    puts it back in the queue instead
  item forwarded <dir> <unit>       the unit's answers are sent: mark its answered items forwarded; refused
                                    unless a forward due line lists the unit
  item drop <dir> <id> <reason...>  drop an item that is no longer needed
  item list <dir>                   current, queued (in order), parked and answered items, one line each, or none
  status <dir>                      the tracker and everything waiting on the manager
  clear-bad <dir>                   forget the bad request lines status lists, once they are handled
  check <dir> <unit>                one readiness check of the unit's PR, printed as one line: READY <pr> <sha>
                                    (two equal clean polls 30 s apart), NOT-READY <pr> head=<sha7><reasons>,
                                    NOT-READY - no open PR from <branch>, MERGED <pr>, CLOSED <pr> or ERROR;
                                    in integration mode READY becomes NOT-READY ... blocked: <reason>
                                    (also for a PR on the wrong base) or FINAL-READY
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

import items
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


def _wrong_base(state: dict, name: str) -> str | None:
    """Why the unit's open PR, as last polled, targets the wrong branch in the integration mode, or None."""
    marks = state["marks"].get(name, {})
    if state["units"][name]["prs"].get(str(marks.get("pr"))) != "OPEN":
        return None
    return runstate.wrong_base(state, name, marks.get("base"))


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
            and not runstate.integration_block(state, name, run_dir)
            and not _wrong_base(state, name)
            and not owner_final
        ):
            pr, _, sha = ready.partition(" ")
            if record["prs"].get(pr) == "OPEN" and marks.get("head") == sha:
                needs.append(f"{_label(state, name)} PR #{pr} ready since {_clock(marks['ready_at'])}")
        wrong = _wrong_base(state, name)
        if wrong:
            needs.append(f"{_label(state, name)} PR #{marks['pr']} {wrong}: tell its child to retarget it")
        go_sha = marks.get("go_sha")
        if go_sha and marks.get("synced_sha") == go_sha:
            # The watcher saw the push land (it marks synced_sha, and with an open PR pops go_head)
            # but `synced` never ran: no SYNCED repeats.
            needs.append(f"integration push landed: run synced {name} {go_sha}")
        elif "go_head" in marks:
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
    needs += items.forward_due(state)
    needs += [f"gate {name} may open" for name in runstate.openable(state)]
    if runstate.final_due(state):
        needs.append(f"final gate for {runstate.integration_unit(state)} may open: run open-ready")
    if runstate.all_stopped(state):
        needs.append(f"every unit stopped: ask the owner what to do with {state['integration']['branch']}")
    return needs


def _integration_lines(run_dir: Path, state: dict) -> list[str]:
    """The status lines of the integration mode; none in a default run."""
    block = state.get("integration")
    if not block:
        return []
    lines = [
        f"- integration: {block['branch']} into {state['base']}"
        f" · final merge: {block['final_merge']} · feature review: {'yes' if block['feature_review'] else 'no'}"
    ]
    name = runstate.integration_unit(state)
    if runstate.sync_in_flight(state, run_dir):
        syncing = state["marks"].get(name, {}).get(runstate.SYNCING_MARK)
        detail = f" (resolving a conflicted merge of {state['base']} since {_clock(syncing['at'])})" if syncing else ""
        lines.append(f"- integration sync in flight{detail}")
    record = state["units"].get(name) if name else None
    open_prs = [pr for pr, status in record["prs"].items() if status == "OPEN"] if record else []
    if open_prs:
        pr = max(open_prs, key=int)
        late = runstate.late_units(state)
        if late:
            lines.append(f"- final PR #{pr} waits on {', '.join(late)}")
        marks = state["marks"].get(name, {})
        # A ready mark from before a unit was added is stale: the owner would merge while unit work is still out.
        if (
            _owner_merges(state, name)
            and not runstate.integration_block(state, name)
            and not _wrong_base(state, name)
            and marks.get("ready_head") == f"{pr} {marks.get('head')}"
        ):
            lines.append(f"- final PR #{pr} ready: the owner merges it")
    return lines


def _item_lines(state: dict) -> list[str]:
    """The status lines of the owner queue: `- items:` always, `- parked:` only when something is parked."""
    all_items = state["items"]["list"]
    by_id = {i["id"]: i for i in all_items}
    current = state["items"]["current"]
    if current is None:
        lines = ["- items: none"]
    else:
        item = by_id[current]
        counts: dict[str, int] = {}
        for queued in items.queue_order(state):
            unit = queued["unit"] or items.RUN_PREFIX
            counts[unit] = counts.get(unit, 0) + 1
        more = f"{sum(counts.values())} more: " + ", ".join(f"{unit} \u00d7{n}" for unit, n in counts.items())
        lines = [f"- items: current {_item_name(item)} {item['summary']} \u00b7 {more if counts else 'nothing more'}"]
    parked = sorted((i for i in all_items if i["status"] == "parked"), key=lambda i: i["seq"])
    if parked:
        entries = [f"{_item_name(i)} {i['summary']}, {i['note']}, since {_clock(i['parked_at'])}" for i in parked]
        lines.append("- parked: " + "; ".join(entries))
    return lines


def _item_name(item: dict) -> str:
    return item["id"] + (f" ({item['ref']})" if item["ref"] else "")


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
    lines += _item_lines(state)
    lines += _integration_lines(run_dir, state)
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

    item = sub.add_parser("item", help="the owner queue: items waiting on the owner")
    item_sub = item.add_subparsers(dest="item_command", required=True, metavar="subcommand")

    def item_command(name: str, help: str) -> argparse.ArgumentParser:
        p = item_sub.add_parser(name, help=help)
        p.add_argument("dir", type=Path, help="the run folder")
        return p

    p = item_command("add", "file an item for the owner")
    p.add_argument("unit", nargs="?", help="the unit the item belongs to")
    p.add_argument("--run-wide", action="store_true", help="a run-wide decision, in place of a unit")
    p.add_argument("--ref", metavar="Q<n>", help="the question's number in the child's session")
    p.add_argument("--kind", required=True, choices=items.KINDS)
    p.add_argument("--blocks", required=True, choices=("yes", "no"), help="whether it blocks the unit's merge")
    p.add_argument("--depends", nargs="+", action="extend", metavar="Q<n>", help="refs whose answers it needs")
    p.add_argument("--summary", required=True, help="one line")
    p.add_argument("--body-file", required=True, type=Path, help="the item's text")
    item_command("current", "show the current item")
    p = item_command("done", "close an item the owner dealt with")
    p.add_argument("id")
    p.add_argument("--answer-file", type=Path, help="the owner's answer, for the child")
    p = item_command("park", "set an item aside")
    p.add_argument("id")
    p.add_argument("note", nargs="+")
    p.add_argument("--with", dest="with_ids", nargs="+", action="extend", metavar="ID", help="park these too")
    p = item_command("unpark", "bring a parked item back")
    p.add_argument("id")
    p.add_argument("--queue", action="store_true", help="rejoin the queue instead of becoming current")
    p = item_command("forwarded", "mark a unit's answered items sent to its child")
    p.add_argument("unit")
    p = item_command("drop", "drop an item that is no longer needed")
    p.add_argument("id")
    p.add_argument("reason", nargs="+")
    item_command("list", "list the open items")

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


def _integration_verdict(run_dir: Path, state: dict, unit: str, line: str) -> str:
    """Turn a READY line into NOT-READY when the integration branch blocks the unit, or FINAL-READY for the owner."""
    verb, _, rest = line.partition(" ")
    if verb != "READY":
        return line
    reason = runstate.integration_block(state, unit, run_dir)
    if reason:
        pr, _, sha = rest.partition(" ")
        return f"NOT-READY {pr} head={sha[:7]} blocked: {reason}"
    return f"FINAL-READY {rest}" if _owner_merges(state, unit) else line


def _read_text(path: Path) -> str:
    """The text of a file the owner or a child wrote; a refusal, not a crash, when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise RunError(f"cannot read {path}: {getattr(exc, 'strerror', None) or exc}") from None


def _item_list(state: dict) -> str:
    """One line per open item: current, queued in order, parked, then answered and not yet forwarded."""
    by_status = {"current": [], "parked": [], "answered": []}
    for item in sorted(state["items"]["list"], key=lambda i: i["seq"]):
        if item["status"] in by_status:
            by_status[item["status"]].append(item)
    lines = [f"current {items.header(i)}" for i in by_status["current"]]
    lines += [f"queued {items.header(i)}" for i in items.queue_order(state)]
    lines += [
        f"parked {items.header(i)} \u00b7 {i['note']} since {_clock(i['parked_at'])}" for i in by_status["parked"]
    ]
    lines += [f"answered {items.header(i)} (not forwarded)" for i in by_status["answered"]]
    return "\n".join(lines) or "none"


def _item(args: argparse.Namespace, now: float) -> str | None:
    """Run one `item` subcommand; the files it names are read before the lock is taken."""
    run_dir: Path = args.dir
    sub = args.item_command
    if sub in ("current", "list"):
        state = load(run_dir)
        return items.current_text(run_dir, state) if sub == "current" else _item_list(state)
    body = answer = None
    unit = ""
    if sub == "add":
        if bool(args.unit) == args.run_wide:
            raise RunError("give exactly one of a unit and --run-wide")
        unit = args.unit or ""
        body = _read_text(args.body_file)
    elif sub == "done" and args.answer_file:
        answer = _read_text(args.answer_file)
    with locked(run_dir) as state:
        if sub == "add":
            return items.add_item(
                run_dir,
                state,
                unit,
                ref=args.ref,
                kind=args.kind,
                blocks=args.blocks == "yes",
                depends=args.depends or [],
                summary=args.summary,
                body=body,
                now=now,
            )
        if sub == "done":
            return items.done_item(run_dir, state, args.id, answer)
        if sub == "park":
            return items.park_item(state, args.id, " ".join(args.note), args.with_ids or [], now)
        if sub == "unpark":
            return items.unpark_item(state, args.id, to_queue=args.queue)
        if sub == "forwarded":
            return items.mark_forwarded(state, args.unit)
        items.drop_item(state, args.id, " ".join(args.reason))
    return None


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
    if command == "item":
        return _item(args, now)
    if command == "check":
        state = load(run_dir)
        record = state["units"].get(args.unit)
        if record is None:
            raise RunError(f"unknown unit {args.unit!r}")
        if not record["branch"]:
            raise RunError(f"unit {args.unit!r} has no branch yet; record it with: unit <dir> {args.unit} --branch B")
        # In the integration mode a PR on the wrong base is never READY.
        base = runstate.pr_base(state, args.unit)
        line = check_once(state["repo"], record["branch"], gh=run_gh, sleep=time.sleep, base=base)
        return _integration_verdict(run_dir, state, args.unit, line)
    with locked(run_dir) as state:
        if command == "unit":
            if args.after and "none" in args.after and len(args.after) > 1:
                raise RunError("--after none clears the list; give it alone")
            after = [] if args.after == ["none"] else args.after
            generation = state["units"].get(args.name, {}).get("generation")
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
            if generation is not None and state["units"][args.name]["generation"] != generation:
                # A new child session: the old session's open items no longer apply.
                return "\n".join(items.drop_old_generation(state, args.name, "new session")) or None
        elif command == "set":
            runstate.set_option(state, args.key, args.value)
            if args.key in ("wip", "queue") and state["queue_mode"] == "parallel":
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
            return "\n".join(items.drop_unit_items(state, args.unit, "unit stopped")) or None
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
        name = f"item {args.item_command}" if args.command == "item" else args.command
        print(f"run.py {name}: {' '.join(_describe(args.dir, exc).split())}", file=sys.stderr)
        return 1
    if out:
        print(out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
