"""The owner queue: items waiting on the human owner, kept in run.json under `items`.

An item is a question, finding, command or decision that a child session or the manager files for the owner.
Its body (and later its answer) is a file in `<run>/items/`; the record in `state["items"]["list"]` holds the
rest. Statuses: `queued`, `current` (the one shown to the owner), `parked` (set aside, see `parked_with` and
`note`), `answered` (waiting to be forwarded to its child), `forwarded`, `dropped`.

Every function mutates `state` in place and the caller holds the run lock. `runstate` never imports this module.
"""

from __future__ import annotations

from pathlib import Path
import re

from runstate import RunError

KINDS = ("question", "finding", "command", "decision")
OPEN = ("queued", "current", "parked", "answered")  # the statuses a drop takes
REF_RE = r"Q[1-9][0-9]*"
ITEMS_DIR = "items"
RUN_PREFIX = "run"  # the id prefix and display name of run-wide items, whose `unit` is ""


def _label(unit: str) -> str:
    return unit or RUN_PREFIX


def header(item: dict) -> str:
    """One line naming an item: id, ref, unit, kind, whether it blocks the merge, what it depends on, summary."""
    parts = [item["id"] + (f" ({item['ref']})" if item["ref"] else ""), _label(item["unit"]), item["kind"]]
    parts.append("blocks merge: " + ("yes" if item["blocks"] else "no"))
    if item["depends"]:
        parts.append("depends on: " + ", ".join(item["depends"]))
    parts.append(item["summary"])
    return " · ".join(parts)


def _find(state: dict, item_id: str) -> dict:
    for item in state["items"]["list"]:
        if item["id"] == item_id:
            return item
    raise RunError(f"unknown item {item_id!r}")


def queue_order(state: dict) -> list[dict]:
    """The queued items in the order the owner will see them, the current item excluded.

    The owner stays on a unit: items of the sticky unit come first, then the resume item (an unparked item), then
    the oldest queued item, whose unit becomes the new sticky unit. Whether an item blocks never changes the order.
    """
    items = state["items"]
    pending = sorted((i for i in items["list"] if i["status"] == "queued"), key=lambda i: i["seq"])
    sticky, resume = items["sticky"], items["resume"]
    out: list[dict] = []
    while pending:
        same = [i for i in pending if i["unit"] == sticky] if sticky is not None else []
        if same:
            pick = same[0]
        else:
            pick = next((i for i in pending if i["id"] == resume), pending[0])
        out.append(pick)
        pending.remove(pick)
        sticky = pick["unit"]
        if pick["id"] == resume:
            resume = None
    return out


def _promote(state: dict) -> None:
    """Make the first queued item current when nothing is; its unit becomes the sticky one."""
    items = state["items"]
    if items["current"] is not None:
        return
    order = queue_order(state)
    if not order:
        return
    first = order[0]
    first["status"] = "current"
    items["current"] = first["id"]
    items["sticky"] = first["unit"]
    if items["resume"] == first["id"]:
        items["resume"] = None


def _current_header(state: dict) -> str:
    """The current item's header; `none` when nothing is current."""
    current = state["items"]["current"]
    return header(_find(state, current)) if current else "none"


def current_text(run_dir: Path, state: dict) -> str:
    """The current item's header, a blank line and its body; `none` when nothing is current."""
    current = state["items"]["current"]
    if current is None:
        return "none"
    body_file = run_dir / ITEMS_DIR / f"{current}.md"
    body = body_file.read_text(encoding="utf-8") if body_file.exists() else ""
    return f"{header(_find(state, current))}\n\n{body}"


def _validate_new(state: dict, unit: str, ref: str | None, kind: str, depends: list[str], summary: str) -> int:
    """Refuse a bad new item; return the generation it belongs to (0 for a run-wide item)."""
    record = None
    if unit:
        record = state["units"].get(unit)
        if record is None:
            raise RunError(f"unknown unit {unit!r}")
        if record["gate"] == "stopped":
            raise RunError(f"unit {unit!r} is stopped")
    if kind not in KINDS:
        raise RunError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if not unit and kind != "decision":
        raise RunError(f"a run-wide item must be a decision, got {kind!r}")
    if ref is not None and not unit:
        raise RunError("a run-wide item takes no ref")
    if ref is not None and kind == "decision":
        raise RunError("a decision takes no ref")
    if ref is not None and not re.fullmatch(REF_RE, ref):
        raise RunError(f"ref must look like Q1, Q2, ..., got {ref!r}")
    if depends and ref is None:
        raise RunError("depends needs a ref")
    generation = record.get("generation", 1) if record else 0
    scope = {i["ref"]: i for i in state["items"]["list"] if i["unit"] == unit and i["generation"] == generation}
    if ref is not None and ref in scope:
        raise RunError(f"ref {ref} of unit {unit!r} is already {scope[ref]['id']}")
    for dep in depends:
        if dep not in scope:
            raise RunError(f"depends ref {dep} is not an item of unit {unit!r} in its current session")
    if not summary.strip() or "\n" in summary or "\r" in summary:
        raise RunError("summary must be one non-empty line")
    return generation


def add_item(
    run_dir: Path,
    state: dict,
    unit: str,
    *,
    ref: str | None,
    kind: str,
    blocks: bool,
    depends: list[str],
    summary: str,
    body: str,
    now: float,
) -> str:
    """File an item for the owner. `unit` is "" for a run-wide item, which must be a decision.

    `ref` (Q1, Q2, ...) names a question within the unit's current child session and may be reused by a later
    session; `depends` lists refs of that session whose answers this item needs. An item that depends on a parked
    item is stored parked with it. Returns `current <header>` when it is shown at once, `parked <id> with <parent>`
    when it is stored parked, else `queued <id> for <unit>, <N> waiting`.
    """
    generation = _validate_new(state, unit, ref, kind, depends, summary)
    items = state["items"]
    scope = {i["ref"]: i for i in items["list"] if i["unit"] == unit and i["generation"] == generation}
    filed = [scope[dep]["id"] for dep in depends]
    parked_on = next((i for i in filed if _find(state, i)["status"] == "parked"), None)
    prefix = _label(unit)
    items["counters"][prefix] = items["counters"].get(prefix, 0) + 1
    items["seq"] += 1
    item_id = f"{prefix}-{items['counters'][prefix]}"
    (run_dir / ITEMS_DIR).mkdir(parents=True, exist_ok=True)
    (run_dir / ITEMS_DIR / f"{item_id}.md").write_text(body, encoding="utf-8")
    record = {
        "id": item_id,
        "unit": unit,
        "ref": ref,
        "generation": generation,
        "kind": kind,
        "blocks": blocks,
        "depends": filed,
        "summary": summary,
        "status": "queued",
        "added": now,
        "seq": items["seq"],
        "note": None,
        "parked_at": None,
        "parked_with": None,
        "reason": None,
    }
    if parked_on is not None:
        record.update(status="parked", parked_with=parked_on, note=f"depends on {parked_on}", parked_at=now)
    items["list"].append(record)
    _promote(state)
    if items["current"] == item_id:
        return f"current {header(record)}"
    if parked_on is not None:
        return f"parked {item_id} with {parked_on}"
    return f"queued {item_id} for {prefix}, {len(queue_order(state))} waiting"


def _release(state: dict, roots: list[str]) -> None:
    """Return to `queued` every parked item parked with one of `roots`, and so on down the chain.

    Clears their note and `parked_with`. The roots themselves are left as they are.
    """
    released = set(roots)
    changed = True
    while changed:
        changed = False
        for item in state["items"]["list"]:
            if item["status"] == "parked" and item["parked_with"] in released and item["id"] not in released:
                item.update(status="queued", note=None, parked_with=None)
                released.add(item["id"])
                changed = True


def park_item(state: dict, item_id: str, note: str, with_ids: list[str], now: float) -> str:
    """Set a queued or current item aside with a one-line note, taking its dependents and `with_ids` along.

    Every queued or current item that depends on a parked item, and each item of `with_ids`, is parked with the
    item (`parked_with` is `item_id`, note `with <item_id>`). If the current item was parked, the next queued item
    becomes current. Returns `parked <id>` per item, the new current item's header (or `none`), then `forward due`
    lines.
    """
    item = _find(state, item_id)
    if item["status"] not in ("queued", "current"):
        raise RunError(f"item {item_id} is {item['status']}")
    for other in with_ids:
        if other == item_id:
            raise RunError(f"item {item_id} cannot be parked with itself")
        status = _find(state, other)["status"]
        if status not in ("queued", "current"):
            raise RunError(f"item {other} is {status}")
    if not note.strip() or "\n" in note or "\r" in note:
        raise RunError("note must be one non-empty line")
    items = state["items"]
    taken = [item]
    item.update(status="parked", note=note, parked_at=now, parked_with=None)
    taken_ids = {item_id}
    changed = True
    while changed:
        changed = False
        for other in items["list"]:
            if other["status"] not in ("queued", "current"):
                continue
            if other["id"] in with_ids or taken_ids.intersection(other["depends"]):
                other.update(status="parked", note=f"with {item_id}", parked_at=now, parked_with=item_id)
                taken.append(other)
                taken_ids.add(other["id"])
                changed = True
    if items["current"] in taken_ids:
        items["current"] = None
    if items["resume"] in taken_ids:
        items["resume"] = None  # unparked later, it rejoins the queue in arrival order
    _promote(state)
    return "\n".join([*(f"parked {i['id']}" for i in taken), _current_header(state), *forward_due(state)])


def unpark_item(state: dict, item_id: str, *, to_queue: bool) -> str:
    """Bring a parked item back, with everything parked with it.

    By default it becomes the current item (and its unit the sticky one) and the item it displaces goes back to the
    queue as the resume item; returns its header. With `to_queue` it only rejoins the queue at its old place;
    returns `queued <id>`, or `current <header>` when nothing else was current and it is shown at once.
    """
    item = _find(state, item_id)
    if item["status"] != "parked":
        raise RunError(f"item {item_id} is {item['status']}")
    items = state["items"]
    item.update(status="queued", note=None, parked_with=None)
    if not to_queue:
        if items["current"] is not None:
            _find(state, items["current"])["status"] = "queued"
            items["resume"] = items["current"]
        item["status"] = "current"
        items["current"] = item_id
        items["sticky"] = item["unit"]
        if items["resume"] == item_id:
            items["resume"] = None
    _release(state, [item_id])
    if to_queue:
        _promote(state)
        return f"current {header(item)}" if items["current"] == item_id else f"queued {item_id}"
    return header(item)


def _forward_groups(state: dict) -> dict[str, list[dict]]:
    """The answered items of each unit with nothing current or queued, by unit in the order of its earliest answer."""
    items = state["items"]["list"]
    busy = {i["unit"] for i in items if i["status"] in ("current", "queued")}
    answered: dict[str, list[dict]] = {}
    for item in sorted((i for i in items if i["status"] == "answered"), key=lambda i: i["seq"]):
        answered.setdefault(item["unit"], []).append(item)
    return {unit: group for unit, group in answered.items() if unit not in busy}


def forward_due(state: dict) -> list[str]:
    """`forward due <unit> <id> ...` for each unit with answers to send and nothing current or queued.

    Units come in the order of their earliest answer. The manager sends the answers, then calls `mark_forwarded`.
    """
    return [f"forward due {unit} {' '.join(i['id'] for i in group)}" for unit, group in _forward_groups(state).items()]


def mark_forwarded(state: dict, unit: str) -> str:
    """Mark the unit's answered items as forwarded; returns `forwarded <id> ...`.

    Refused unless `forward_due` lists the unit: its answers go out together, once the owner has left the unit.
    """
    group = _forward_groups(state).get(unit)
    if group is None:
        if not any(i["unit"] == unit and i["status"] == "answered" for i in state["items"]["list"]):
            raise RunError(f"unit {_label(unit)!r} has no answered items")
        raise RunError(f"unit {_label(unit)!r} is not forward due: it still has a current or queued item")
    for item in group:
        item["status"] = "forwarded"
    _promote(state)
    return "forwarded " + " ".join(i["id"] for i in group)


def done_item(run_dir: Path, state: dict, item_id: str, answer: str | None) -> str:
    """Close an item the owner has dealt with; returns the next item's header (or `none`) and `forward due` lines.

    A decision needs no answer and goes straight to `forwarded`, since no child waits for it; any other item needs
    an answer, which is saved beside its body, and becomes `answered` until the manager forwards it.
    """
    item = _find(state, item_id)
    if item["status"] not in ("current", "queued", "parked"):
        raise RunError(f"item {item_id} is {item['status']}")
    if answer is None and item["kind"] != "decision":
        raise RunError(f"item {item_id} needs an answer")
    items = state["items"]
    if answer is not None:
        (run_dir / ITEMS_DIR).mkdir(parents=True, exist_ok=True)
        (run_dir / ITEMS_DIR / f"{item_id}.answer.md").write_text(answer, encoding="utf-8")
    item["status"] = "forwarded" if item["kind"] == "decision" else "answered"
    if items["current"] == item_id:
        items["current"] = None
    if items["resume"] == item_id:
        items["resume"] = None
    _release(state, [item_id])
    _promote(state)
    return "\n".join([_current_header(state), *forward_due(state)])


def drop_item(state: dict, item_id: str, reason: str) -> None:
    """Drop an item that is no longer needed, with the reason; refused once it is forwarded or already dropped."""
    item = _find(state, item_id)
    if item["status"] in ("forwarded", "dropped"):
        raise RunError(f"item {item_id} is {item['status']}")
    _drop(state, item, reason)
    _release(state, [item_id])
    _promote(state)


def _drop(state: dict, item: dict, reason: str) -> None:
    item["status"] = "dropped"
    item["reason"] = reason
    if state["items"]["current"] == item["id"]:
        state["items"]["current"] = None
    if state["items"]["resume"] == item["id"]:
        state["items"]["resume"] = None


def _drop_open(state: dict, doomed: list[dict], reason: str) -> list[str]:
    _release(state, [i["id"] for i in doomed])
    for item in doomed:
        _drop(state, item, reason)
    _promote(state)
    return [f"dropped {i['id']}" for i in doomed]


def drop_unit_items(state: dict, unit: str, reason: str) -> list[str]:
    """Drop the unit's queued, current, parked and answered items; returns `dropped <id>` lines."""
    doomed = [i for i in state["items"]["list"] if i["unit"] == unit and i["status"] in OPEN]
    return _drop_open(state, doomed, reason)


def drop_old_generation(state: dict, unit: str, reason: str) -> list[str]:
    """Drop the unit's queued, current, parked and answered items from its earlier sessions; returns `dropped <id>`.

    A new child session (a new generation) never sees the old session's questions, and its refs start again at Q1,
    so an old answer forwarded to it would be read against the wrong question.
    """
    generation = state["units"][unit].get("generation", 1)
    doomed = [
        i for i in state["items"]["list"] if i["unit"] == unit and i["status"] in OPEN and i["generation"] != generation
    ]
    return _drop_open(state, doomed, reason)
