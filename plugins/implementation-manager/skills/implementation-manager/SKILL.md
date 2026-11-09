---
name: implementation-manager
description: Use when the user asks this session to be the implementation manager, or to run a planned multi-phase or multi-sub-phase piece of work to completion through child sessions that each deliver a pull request, either in a dedicated session or right after planning the work here.
argument-hint: "[spec, plan or epic path]"
---

# Implementation Manager

<!-- Editing note: never write a dollar sign followed by a digit in this file. Claude Code replaces it with a skill argument when the skill loads. Only this file is substituted, so every placeholder and the arguments line stay here, never in references/. After a compaction only the first 5,000 tokens of this file come back: keep it under 17,500 characters (tests/test_skill_text.py), most important first. -->

This session takes planned work from spec to merged code. It starts one child session per unit of work (a phase or sub-phase), clears each child's pushes against branch-review's state, merges each child's pull request when it is ready, and releases the units that depend on it. It writes no product code. Its own planning PR, if one is open, is the one PR it works on itself: it keeps Auto-fix on for it, fixes every finding (thread or review body), reviews it with `/branch-review:review` and gates each push with `branch-review:check`, like a child's. The user is needed only for questions and problems.

**Invoking this skill authorizes** starting the child sessions and merging the run's PRs (the children's, cards the user starts from this run, and this session's own planning PR) once they meet the merge bar. It authorizes nothing else. When the owner asks for integration-branch mode, that request also authorizes creating the integration branch and deleting it once the final PR merges.

Arguments: `$ARGUMENTS`

## Four constraints that shape everything

1. **Sessions can only be started on the user's own turn.** `start_session` with `"user_asked"` is refused on a turn started by a background task, a child's note or another session's message; it degrades to a card the user must click. So **start every child in the first turn**, and hold each one back with a *gate* (a file the child polls), not by starting it later.
2. **Messages are a scarce budget.** The app stops delivering a session's messages after 10 sends without the user typing in it. Coordinate through files in the run folder (gates, push requests and answers), never through status messages.
3. **Nothing notifies you of a push request, a ready PR or a merge** except the run watcher: `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py wait <run> --full` with `run_in_background`. It prints one event and the lines you need to act on it, then exits. Handle the event, then start it again before ending the turn. With open units and no `wait` running, the run is deaf.
4. **Child notes carry nothing you need.** The app sends you a note each time a child's turn ends, and a child ends a turn at every subagent dispatch, so they run to dozens per child. It may also hold them for hours and deliver only the latest. Nothing waits on a note: children write push requests to `<run>/requests.log`, send questions and `[blocked]` as messages, and you act on the watcher's lines, those messages and `run.py status`. So you detach each child once it is recorded (references/setup.md, step 6), which stops its notes.

## The run script and the run folder

`run.py <command> <run> <args>` is short for `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py <command> <run> <args>`: `<run>`, the absolute run folder, always comes right after the command (for `item`, after its subcommand: `run.py item add <run> <unit>`). Write it out in full each time; shell variables do not survive between Bash calls. Only the script writes `run.json`, `gates.log` and `pushes.log`; never write them yourself. A refused command exits 1 with one line on stderr and changes nothing. The commands most wakes need (every command, with its options and output, is in references/commands.md):

| Command | Does |
|---|---|
| `status <run>` | The tracker and every line under "needs the manager"; its last line may be `- context: <N>k`. |
| `wait <run> --full` | Blocks until one event and prints it with its `verdict`, log and `status` lines ("The wake loop"). Always with `run_in_background`. |
| `go <run> <unit> <sha>` | Allows the push. Serial mode: takes the slot, and refuses while another unit holds it or heads the queue, or the unit merges after a unit not yet merged. |
| `queue <run> <unit> <sha>` | Holds a covered request until the slot frees; the watcher then reports `NEXT`. |
| `redo <run> <unit> <sha> <reasons>` | Refuses the push with one line of reasons. |
| `hold <run> <unit> <reason>`, `release <run> <unit>` | Holds the unit's merge (frees the slot, answers waiting requests `redo <sha> held`); lifts the hold. |
| `check <run> <unit>` | One readiness check of the unit's PR now. |
| `merged <run> <unit> <pr> [--more]` | Records a merge (`--more`: a part k of N with k < N) and frees the slot. |
| `item add`, `item current`, `item done`, `item list` | The owner queue (references/owner-queue.md). |

`<run>` is `${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/<slug>`, `<slug>` a slug of the work (e.g. `query-gen-phase-5`). It is an absolute path; write it out in full wherever `<run>` appears. If that path does not start with `/`, the plugin's data folder is unknown: stop and tell the user, and never pick a folder of your own. Setup prunes old runs before `init` with `run.py prune <run> --days '${user_config.retention_days}'` (references/setup.md, step 3).

The rest of these instructions are reference files in `${CLAUDE_SKILL_DIR}/references/`. Read the one for what you are about to do with Read, before you do it ("Situation → read", at the end).

## After a compaction

A turn that finds the conversation compacted (a compaction summary), or that has no memory of the run's details, first reads `<run>/notes.md`, its `## Decisions` section included, and runs `run.py status <run>`, before acting. Then it reads the reference file for what it is about to do. Never re-run Setup and never start sessions again: every child already has one. A `wait` started before the compaction is still running and its output will wake you; start another only if none is running.

## The wake loop

Every turn: handle what woke you, then make sure exactly one `run.py wait <run> --full` is running before you end the turn (start one when the last one has printed its output, or when you cannot tell whether it survived an app restart). Its output is the event line; for `REQUEST`, `PENDING`, `NEXT`, `READY` and `REMIND` a `verdict` line; for a `STUCK` event `pushes:` and `requests:` lines (the unit's last line in each log); then an empty line and the `status` block. Handle the event with those lines (references/events.md has a row for each event): where a row or the merge bar reads `run.py status`, the printed block is that status, unless you have since run a command that changes the run. If it printed `status unavailable: …` instead, run `run.py status <run>` yourself. A user message has no `wait` output: run `run.py status <run>` first ("On every user message", references/events.md). Unless a row says otherwise, the whole reply is one short line, with nothing about a child's progress.

## The verdict rule

The `verdict` line is `verdict <sha> {…}`, branch-review's `rs covered` JSON (`covered`, `reasons`, `branch`, …), or `verdict <sha> unavailable: <reason>`. A JSON verdict for the event's own sha is the check, in place of invoking `branch-review:check`:
- For a push request (`REQUEST`, `PENDING`, `NEXT`), it is step 3 of "Answering a push request" (references/events.md), whose other steps still apply in order: covered gets `go` (or `queue`), not covered gets `redo` with its `reasons`.
- For `READY` or `REMIND`, it satisfies the merge bar's check only when its sha is the event's head sha.
- `unavailable`, or a verdict for any other sha: invoke `branch-review:check --repo <worktree> --commit <sha>`. If the reason is `rs not found: <path>` (branch-review was updated) or `no check command recorded` (a run begun before 0.10.0): re-record its paths with `run.py check-cmd <run> --rs <rs path> --state-dir <state folder>`, from the paths the loaded check skill shows.

## Asking the owner to compact

When the last line of `status` (the block `wait --full` printed, or `run.py status <run>`) ends `, compact due` and a `wait` is running, run `run.py compact-asked <run>` and make this line, exact, with `<run>` written out, the last of the reply, after any item or tracker the reply ends with:

`/compact Resume as the implementation manager for <run>: read notes.md, then run.py status, before acting.`

## Decisions

An owner decision that is not an item answer (a WIP change, a scope ruling, a "do X for every unit") goes into `<run>/notes.md` under `## Decisions` in the same turn: one line, `YYYY-MM-DD: <decision>`. Item answers already live in `<run>/items/`.

## Red flags: pushes and merges

| Thought | Reality |
|---|---|
| "Merges notify me" | They don't, and passing checks don't either. Only `run.py wait <run> --full` tells you. |
| "The wait printed its line; I'll restart it later" | Restart it before ending the turn. Without it the run hears nothing. |
| "All checks pass, merge" | Also: covered on *this* head (a `verdict` for this exact head sha from `wait --full`, or `branch-review:check`), every other merge bar line, and no unanswered request. |
| "The PR is ready but the child is running" | Merge if the bar is met. The child cannot push without a `go`. |
| "The child says its review is clean" | Check it yourself: the same-sha `verdict` line, or `branch-review:check`. |
| "It's only a clean sync, it needs a review" or "it's only a merge from the base, no check needed" | The check decides both: a clean base merge is covered, a conflicting one says `base-conflict`. |
| "The Review notes list findings outside the change, and this repo forbids deferring; refuse the push" | `outside` is not a deferral (`deferred` means the owner chose it), and the check decides the push and the merge. Where the repo forbids deferring, the review escalates outside findings and the check says `escalated`; relay them like any escalation. If such a repo's Review notes still list outside findings the check does not count as escalated, give them to the owner as a question, and do not hold the push. |
| "The next queued unit should sync first" | The `go` covers it: the child syncs right before it pushes. |
| "I'll append the go to `pushes.log` myself" or "I'll message the go" | `run.py go`; it keeps the slot and the queue. Never a message. |
| "The holder is waiting on the owner; it keeps the slot" | A blocking question or an escalation: `run.py hold`, which frees the slot. It asks again once you forward the answer. |
| "I'll hold this PR until the owner answers" | `run.py hold`, never memory. Only `hold` stops the watcher reporting it `READY`. |
| "I forwarded the answer, so the hold can go" | `release`, then `run.py hold <run> <unit> answer sent: <topic>`. The old head stays unmergeable until the child asks again. |
| "Auto-fix woke the child; it can push the fix" | It asks first, every time. |
| "The final PR is ready; merge it" | Only when the owner chose `manager`. `FINAL-READY` is never merged: the owner merges it. |

## Situation → read

| Situation | Read |
|---|---|
| The first turn of a run: Setup, integration-mode setup | references/setup.md |
| Starting a child, a unit the user adds or the owner designs, a card | references/children.md |
| A watcher event, a user message, a child's message, answering a push request, messaging a child | references/events.md |
| `READY` or `REMIND`, merging, after a merge | references/merge-bar.md |
| Filing, showing, answering, parking or forwarding an owner item; the owner answers | references/owner-queue.md, then the "user answers" row in references/events.md |
| Cards, added units, ORPHANs or fixes in integration mode | references/integration.md |
| The `specs` unit; the tracker (after setup, after every merge, whenever something waits on the user); finishing the run | references/finishing.md |
| A command's options or output | references/commands.md |
