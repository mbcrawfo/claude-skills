# D-0001: Implementation manager runs of more than about 10 units

## Planned

The [implementation manager phase 8 review design](../superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md) looked at keeping the manager session's context within bounds on long runs. On SqlSource's phase 8 (8 units, about 25 hours, 2026-10-24 to 2026-10-25), the manager's context reached about 331K tokens over 77 turns, with no compaction. It also read about 68M cached tokens, because every wake-up re-reads the whole context. A run of 15 or more units would probably reach compaction, and the skill's rules and the run's state might not survive it intact.

Where the context went:

- Notes the app delivers when a child ends a turn: 48 of the 77 wake-ups, about 12% of the context. Most needed no action.
- The child prompts the manager writes at setup, about 10K characters each: about 9%.
- PR bodies printed after each merge: about 5%.
- The rest is the app's own system prompt, tool definitions and attachments.

A structural fix was considered: the manager hands off to a fresh manager session partway through a long run, and the new session rebuilds its state from the run folder.

## Delivered instead

Only trims, from the same design: PR bodies go to a file instead of the transcript, and the manager's replies to child notes stay to one line. See [`plugins/implementation-manager/skills/implementation-manager/SKILL.md`](../../plugins/implementation-manager/skills/implementation-manager/SKILL.md).

## Why deferred

The owner judged that one manager running more than about 10 children is an edge case (2026-10-25). The trims cover the runs seen so far.

## Remaining work

Let a run outlive one manager session. The state the scripts own is already in the run folder (`run.json`, `gates.log`, `requests.log`, `pushes.log`, `notes.md`). What is missing:

- A trigger: a turn count, or a context size if one is observable, at which the manager hands off.
- A hand-off: the old manager starts a fresh session that loads the skill against the same run folder. A `start_session` is refused on a background-event turn, so the hand-off must happen on a user turn, or else become a card for the user.
- How children reach the new manager: they hold the old manager's session id for questions.
- How the old manager stops cleanly: its `run.py wait` must exit, and it must not answer requests once the new manager starts.

## Trigger

A planned run of more than about 10 units, or a manager session that compacts mid-run.
