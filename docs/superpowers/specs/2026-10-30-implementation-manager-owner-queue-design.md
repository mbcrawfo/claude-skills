# Implementation manager owner queue: design

Date: 2026-10-30. Plugin: implementation-manager (0.7.1 today; this ships as 0.8.0).

## Intent

When several children are active, the manager relays each child's question to the owner the moment it arrives. Questions pile up while the owner is mid-answer, and an owner returning from AFK scrolls back through four or five items from different sessions. The owner wants to work through them one at a time, finishing one session's items before switching to the next, so they keep one session's context in their head at a time.

"Item" means anything that waits on the owner, not only questions: an escalated finding, a child that needs the owner to run a command, a manager decision such as merging or closing an ORPHAN PR.

Success: at any moment the owner sees exactly one item in full, a count of what else waits, and a list of what they parked. Nothing is lost when the manager's context is summarized.

## Goals

- One current item at a time, shown in full; everything else is queued behind it.
- Items grouped by session (unit); the owner finishes the current unit's items before the next unit's.
- Items the owner cannot answer yet can be parked, out of the queue, and brought back.
- Dependent items move with the item they depend on.
- The queue lives in `run.py` state, so ordering is code, unit-tested, and survives context compaction.

## Non-goals

- Priority by blocking. A "blocks merge: yes" item does not jump the line (owner's call: an owner who starts answering clears everything queued, so jumping gains little).
- A new transport. Children keep sending items as messages to the manager; no `asks/` files, no watcher event for items. (Considered as approach 2 below; could come later.)
- Reminders for parked items beyond the tracker line.
- The pop-up question tool. Items still go in the reply text (it blocks event handling).

## Anti-goals

- An item silently lost or hidden: every open item is in `status`, current, queued or parked.
- The owner's answer applied to the wrong item.
- The queue burning the children's 10-send message budget faster than today.

## Approaches

1. **Queue in `run.py` (chosen).** New `item` commands; state in `run.json`, bodies in `<run>/items/`. The script computes the order. Children keep messaging, with a small structured header per item. Matches how holds and the push queue already work; ordering is tested code and survives compaction. Cost: a new module, its tests, SKILL.md changes.
2. **Children write item files.** As 1, but children write `<run>/asks/<unit>-Q<n>.md` and the watcher reports `ASK`. Machine-readable fields and maybe a saved message, but a bigger child-protocol change and a second path for children to get wrong, for no gain in ordering.
3. **Prompt only.** A Queue section in `notes.md` and ordering rules in SKILL.md. Cheapest, but an LLM applying sticky grouping and dependency parking by hand drifts, especially after compaction, and SKILL.md is already 346 lines.

The owner picked 1.

## Behavior (decided with the owner)

### Ordering

- Items are grouped by unit, in arrival order within a unit.
- **Sticky unit.** Once the owner is on unit A, any A item still queued, including one that arrives while they work on A, comes before any other unit's item, however long that has waited.
- When the current unit has nothing queued, the next unit is the one whose **oldest queued item** has waited longest. Waiting time counts from a unit's oldest item, so a unit that keeps adding items does not push others back. *(Manager proposal, accepted in playback.)*
- Blocking status never changes the order.

### Presenting

- The manager shows one item: unit, kind, the question or the command to run and why, the child's recommendation and the manager's own. One line under it: `3 more waiting: A ×1, B ×2`, and a parked count if any.
- An item arriving while one is current: the reply is one line, `queued for B, 4 waiting`. The current item does not change.
- An item arriving with nothing current becomes current and is shown in full.
- **AFK return.** On any owner message that is not about the current item (for example "back", "what's next", or an unrelated instruction), the manager handles it and ends the reply by showing the current item again in full. *(Manager proposal, accepted.)*
- "What's waiting?" gets one line per open item: current, queued in order, then parked.

### Closing an item

- **Answered:** the manager forwards the owner's words to the child (see "Forwarding" below), closes the item, and shows the next one. A reply that is a question back ("why does the reviewer think that?") keeps the item current; the manager answers first.
- **Command:** the item shows the exact command and why. When the owner says it is done, the manager tells the child and closes the item; pasted output goes to the child too.
- **Follow-up from the child:** an item the child sends because of an answer is a new item of that unit. If the owner is still on that unit, it comes next by the sticky rule; otherwise it waits like any other.
- **Stale:** when a unit is stopped, its open items are dropped and the owner is told in one line per item. See "Calls I made" for merges.

### Parking

- The owner says something like "park this, need to ask Sam". The item leaves the queue for a parked list, with the owner's note. The child is not told (saves its budget). A unit hold stays as it is.
- Parking also parks every open item marked as depending on it (transitively). Where an unmarked item plainly builds on the parked one, the manager parks it too and says so in one line; "keep it" puts it back in the queue.
- The tracker gains a `Parked:` line, e.g. `Parked: 5-3-Q2 (column naming), check with Sam, since 14:05`, in every tracker. No other reminder.
- **Unparking:** "back to 5-3-Q2" at any time makes it current at once, ahead of the queue. Its dependents return to the queue in their original order, and the sticky rule brings them next. The item that was current goes back to the front of the queue: once the unparked unit has nothing queued, it is shown next, before the oldest-unit rule applies.
- Parked items drop on stop, like queued ones.

### Item names and dependencies (child side)

- Each child numbers its own items `Q1`, `Q2`, … for the life of its session and never reuses a number. It chooses them because it cannot learn manager-assigned names without spending messages.
- The script assigns every filed id: `<unit>-<n>`, numbered per unit across the run (e.g. `5-3-2`), and `run-<n>` for run-wide items (for example "every unit stopped"). A child item also records the child's own number as its `ref` (`Q2`) and the unit's **session generation**. The owner sees both: `5-3-2 (Q2)`.
- A unit's session generation starts at 1 and goes up by one whenever `run.py unit <name> --worktree P` records a worktree different from the one stored: that is a new child session (a unit redone after `CLOSED`, a card replacing it), which numbers its items from `Q1` again. `--depends Q1` resolves to the item with that `ref` in the unit's current generation, so a new session's `Q1` never collides with the old one's.
- Every item a child sends starts with one header line: `Q3 · kind: question|finding|command · blocks merge: yes|no · depends on: Q1` (`depends on` left out when there is none). An escalated finding is `kind: finding` and needs no `blocks merge`, as today.
- An item with no header (a child prompted before 0.8.0, in a run that upgraded mid-way) is filed with no `ref` and no dependencies; its kind and blocking status are read from its text as today (a question with no "blocks merge" marker blocks).
- A message with several items (one review round's findings go in one message) is split into one item each, in the message's order.

## Design

### State

`run.json` gains a top-level `items` key (backfilled by `load` as an empty structure for runs already in progress; `STATE_KEYS` stays as it is so old runs still load). Each item records at least:

- `id`, `unit` (or the run-wide group), `ref` and `generation` for child items, `kind` (`question`, `finding`, `command`, `decision`), `blocks` (bool), `depends` (ids), `summary` (one line), `status` (`queued`, `current`, `parked`, `answered`, `forwarded`, `dropped`), `added` time, an arrival sequence number, `note` and `parked_at` when parked, the reason when dropped.

Plus the run-wide fields the ordering needs: the current item's id, the sticky unit, and the "resume" item (the one displaced by an unpark). Each unit record gains its session `generation`. Forwarded and dropped items can stay in state for history or be pruned; builder's call, as long as ids are never reused.

Item bodies (the full text shown to the owner: question, fix options quoted word for word, recommendations) are stored in `<run>/items/<id>.md`, and the owner's answers in `<run>/items/<id>.answer.md`. The script writes both from files the manager passes; `run.json` stays script-only. So an answer given but not yet forwarded survives compaction.

### Commands

`run.py item <subcommand> <run> …`, following the existing CLI conventions (absolute run folder right after the command; refusals exit 1 with one stderr line and change nothing):

| Command | Does |
|---|---|
| `item add <unit\|--run-wide> [--ref Q<n>] --kind K --blocks yes\|no [--depends Q<n> …] --summary S --body-file P` | Queues an item and prints its filed id. Refuses an unknown unit, a `--ref` already used in the unit's current generation, a `--depends` ref not in it, and `--ref`/`--depends` on a run-wide or manager item. Prints `current <id>` if it became current, else `queued <id> for <unit>, <N> waiting`. An item depending on a parked item is parked with it at once. |
| `item current` | Makes the head current if nothing is, then prints the current item's header line and body; `none` when nothing is open. |
| `item done <id> [--answer-file P]` | Marks the item `answered`, storing the owner's words (a `decision` the manager acts on itself needs no answer file and goes straight to `forwarded`). Accepts the current item or a queued one: the owner may answer out of order. Prints the new current item's header, or `none`, and any `forward due` lines (below). |
| `item park <id> <note> [--with ID …]` | Parks the item, its marked dependents and any `--with` items. Prints each parked id, then the new current item and any `forward due` lines. |
| `item unpark <id> [--queue]` | Makes a parked item current, displacing the current one to the front ("resume"); its dependents return to the queue. With `--queue`, puts it back in the queue in its original place instead (the "keep it" case). |
| `item forwarded <unit>` | Marks every `answered` item of the unit `forwarded`, once the manager has sent them. |
| `item drop <id> <reason>` | Drops an open item. |
| `item list` | One line per open item: current, queued in order, parked, then answered but not forwarded. |

`forward due <unit> <ids>` is printed, and listed by `status` under "needs the manager" until `item forwarded` runs, when a unit has `answered` items and no item of it is current or queued (it may have parked ones). That covers the owner moving to another unit, the queue emptying, the rest of the unit being parked, and an out-of-order answer to a unit the owner is not on, which is due at once if that unit has nothing else queued. Run-wide items are always `decision`s, so nothing run-wide is ever forwarded.

`stop` drops the unit's open and answered items and prints `dropped <id>` per item. `status` adds an `- items:` line (current id and summary, count by unit) and a `- parked:` line.

### Ordering function

One pure function picks the head: the resume item if the sticky unit has nothing queued and a resume item is set; else the sticky unit's earliest queued item; else the earliest queued item of the unit whose earliest queued item is oldest. Run-wide items form their own group under the same rules. This function carries most of the unit tests.

### Forwarding and the message budget

Today an owner who answers two of A's questions in one reply costs one message to A. One-at-a-time answering would cost two. So the manager **forwards a unit's answers in one message when the script says `forward due`**, reading the answers from `<run>/items/`, then runs `item forwarded <unit>`. The forward quotes each answer with the child's `ref`. `command` items wait for the batch like the rest: the owner stays on one unit for minutes, not hours. The hold handling of "The user answers a question you relayed" (release, then `hold <unit> answer sent: <topic>`) runs at forward time, once the unit has no open blocking item left.

### Holds

Holds keep their current meaning and commands; items do not replace them. What changes is the record of "the unit's open questions": SKILL.md's references to open questions in `notes.md` become the unit's open items in `run.py item list`. A blocking item still triggers `run.py hold` as today, and a hold is released only when the unit has no open blocking item (queued, current or parked) and nothing answered still waiting to be forwarded.

### What waits on the owner outside the queue

Two things wait on the owner but are not items, and keep their own tracker line, `Elsewhere:`, so the tracker never loses them:

- **Questions in a designed unit's own session.** Their answer belongs in the child's session, so the queue never presents them and nothing is forwarded. They stay where they are recorded today: the unit's open questions in `notes.md`, marked `own session`, with their hold; "Answering a push request" step 5 strikes them as today. The tracker line names the unit and the topic: `Elsewhere: 9-1 own session (cache key shape)`.
- **Starts that came back as cards** at setup or when the owner adds a unit: `Elsewhere: card for 6-2 waiting for you to start it`. Removed once the app reports it started.

Every other hold maps to a blocking item, so it no longer needs its own entry under "Waiting on you".

### SKILL.md changes

- "Questions to the owner": items go through the queue; show one at a time as above.
- Event table rows that today relay to the owner (escalated finding or question, `[blocked]`, `FINAL-READY`, `ORPHAN`, `CLOSED`, `pr-merged` follow-up, `check-error`, `no-worktree-path`, failed merges, "every unit stopped", the integration-mode setup choices once a run exists) add an item instead of writing the question into the reply.
- "The user answers a question you relayed": close the item, show the next, forward per unit as above.
- "On every user message": after `status`, apply the AFK-return rule.
- Tracker: "Waiting on you" becomes the current item and the count; `Parked:` and `Elsewhere:` lines are added.
- Every place that records a unit's worktree (`unit --worktree`) notes that a changed worktree starts a new session generation.
- Child prompt parts 8 and 10: the `Q<n>` numbering and the header line, with `depends on` whenever an item builds on an earlier one.
- Red flags: "I'll show all three new questions now" → one at a time; "I'll forward this answer now" → when the owner leaves the unit.

### Docs, tests, version

- `ARCHITECTURE.md`: an "Owner queue" section (state, commands, ordering, forwarding).
- `README.md` (STE): one bullet under "During the run" on items one at a time and parking.
- Unit tests in `plugins/implementation-manager/tests/` for ordering (sticky unit, oldest-unit choice, resume after unpark), parking with dependents, `forward due` (leaving a unit, empty queue, parked rest, out-of-order answer), session generations and ref resolution, drops on stop, the CLI's refusals, and `load` backfilling `items` for an old run.
- An eval case under `evals/` that gives the manager a run with queued items from two units and an owner answer, and grades that it records the answer (`run.json` shows it `answered`), shows the next item only, and does not forward yet. If an eval case proves impractical, record it in `docs/deferred` in the same PR.
- `plugin.json` version 0.8.0.

## Calls I made (not discussed with the owner)

- **Merge does not drop items.** The playback said a merged unit's items drop. A non-blocking question on a merged unit may still be a valid follow-up (today's rule turns such an answer into a card), and manager items such as the `pr-merged` follow-up or an ORPHAN are created after the merge. So only `stop` drops automatically; after a merge the manager drops items that no longer matter and says so in one line.
- **Forwarding is batched per unit**, to keep the message budget no worse than today, and the script stores answers and says when a forward is due, so a compacted manager loses nothing. `command` items wait for the batch too.
- **Owner may answer a queued item out of order** (`item done` accepts it), since an owner who already knows an answer should not have to wait for its turn. That answer is due to forward at once if the owner is on another unit.
- **Script-assigned ids with the child's `Q<n>` as a ref, scoped to a session generation** that a changed worktree bumps, so a redone unit's new child can start again at `Q1`.
- **Items with no header** (a run upgraded mid-way) are filed without ref or dependencies.
- **Own-session questions and card starts stay outside the queue**, on an `Elsewhere:` tracker line.
- **Kinds** are `question`, `finding`, `command`, `decision` (the last for manager-originated items).
- **`--run-wide` group** for items that belong to no unit.
- **Holds stay separate** from items rather than being driven by them, to keep this change out of the push queue's logic.

## Left to the builder

- Exact `run.json` schema, pruning of closed items, and the CLI's output wording beyond what is above.
- Whether `item` is one module (`items.py`) or part of `runstate.py`.
- How the manager detects that an unmarked item "plainly builds on" a parked one (prompt guidance only).
