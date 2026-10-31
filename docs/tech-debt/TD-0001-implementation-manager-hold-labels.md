# TD-0001: Implementation manager hold labels can lag the real state

## Problem

A unit has one hold, with one reason, and the reason is what `run.py status` shows (`hold <unit>: <reason> since HH:MM`). The manager's rules in [`SKILL.md`](../../plugins/implementation-manager/skills/implementation-manager/SKILL.md) ("Event handling") relabel a hold only in some cases, so the label can name something the unit is no longer waiting on:

- **An owner answer while an own-session question is open.** The unit is held for a relayed question, then a designed unit's child sends an own-session notice; the hold stays as it is. When the owner answers the relayed question, an own-session question is still open, so the manager keeps the hold, and its reason still names the question just answered.
- **A second question under an own-session hold.** A unit held `own session: <topic>` relays a second blocking question; `hold` refuses (`already held`) and the reason stays `own session: <topic>`, though the unit now also waits on the relayed question.

## Why it exists

The behaviour is correct: the unit stays held until its next push request finds no open blocking item of the unit in `run.py item list` (current, queued, parked, or answered and not forwarded) and no `own session` question left in `notes.md` (step 5 of "Answering a push request"), whatever the label says. Keeping every label exact would need a list of reasons per hold, or more relabel rules for the manager to follow, for a display-only gain.

## Impact

Low. Only the `hold <unit>: <reason>` line of `run.py status` differs. Holds no longer list under "Waiting on you": that tracker line comes from the owner queue's items, and an `own session` question shows under `Elsewhere:` from `notes.md`, so a stale label does not change either. The one tracker text a label drives is `<unit> held until it asks again`, for an `answer sent:` reason, and the manager relabels that hold when a blocking item or an own-session notice arrives. Nothing merges early or waits longer.

## Proposed fix

Keep a list of open reasons on each hold: `hold` on a held unit appends a reason instead of refusing, a new `run.py unhold <unit> <topic>` removes one, and the hold lifts when the list empties. `status` lists every open reason, so the manager stops relabelling by `release` then `hold`.

## Trigger

A manager acting on a stale hold label in a real run, or the next change to how holds are placed or released.
