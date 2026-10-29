# implementation-manager architecture

How the manager, its child sessions and the run script work together. This page is for contributors; users start at the [README](README.md).

The manager drives `scripts/run.py`:

```
python3 scripts/run.py <command> <run folder> [args]
```

It exits 0 on success, 1 when a command is refused (one line on stderr, nothing changed) and 2 on a usage error. Its commands, options and refusals are listed in the module docstring at the top of that file, and in `python3 scripts/run.py <command> --help`.

## The push protocol

Pushes go through the manager. A child reviews its change with `/branch-review:review` until it converges, then asks to push by appending `<unit> push <sha> <branch>` to the run's `requests.log` and ending its turn with `[push-request] <sha>`. The run watcher reports the request; the manager runs `branch-review:check` on that commit in the child's worktree and answers through the run script, which appends `<unit> go <sha>`, or `<unit> redo <sha> <reasons>` with the check's reasons joined by `, `, to `pushes.log`. The child's poll reads only lines added after it asked, so it can ask again with the same sha. An Auto-fix wake-up in a child (CI failure, review comment, merge conflict) is never permission to push.

Before its last validation ahead of a request, and before it applies an owner's answer, a child syncs with the base if the base moved, so the sync on `go` is usually a no-op. On `go`, the child syncs with the base right before it pushes. A clean merge is pushed after validation, with no new request; the review check counts a clean base merge as covered. A conflicting merge, or a failed validation, sends the child back to ask again; for a conflicting merge the check reports `base-conflict`, and the child's review checks the resolutions in a small merge-check round.

The child copies the review's generated Review notes block (`review-notes.md`) into its PR description itself. Findings outside the change are not deferrals: where the repository forbids deferring review findings, the review escalates them, and the check reports `escalated` until the owner answers. The child prompt's review command has no `--no-second-opinion`, so a second opinion the review calls for always runs; until it does, the check reports `second-opinion-pending`.

A child asks the owner, through the manager, as soon as it meets a choice the spec does not settle that changes what a test guarantees, behavior users can see or the unit's scope; a plan workflow's own rulings cover implementation choices only. `/branch-review:review` is the child's review, so it skips a plan workflow's per-task code-quality reviews and runs the full validation once the change is complete, unless the repository requires it per commit.

### Detached children

The app sends the manager a note each time a child's turn ends, and a child ends a turn at every subagent dispatch, so notes run to dozens per child and carry nothing the manager acts on. The app can also hold them for hours. So the manager detaches each child once it has recorded the child's branch and worktree, which stops its notes, and nothing in the protocol waits on one: requests are lines in a file, questions and `[blocked]` turns are messages, and the manager learns everything else from the run watcher. Detached children are not nested under the manager in the sidebar, and are not archived with it; the manager offers to archive them by id at the end.

### Child briefs

Child sessions run their commands in the foreground (Bash `timeout` 600000), and paste a fixed block into every implementer and fixer brief that says the same, forbids recording tech debt or follow-ups without the owner, and keeps hand-backs short. A subagent waiting on a background command costs the child a turn for nothing. Escalated findings reach the owner with their fix options quoted word for word, and an owner answer applied only in part goes back to the manager before the child asks to push.

## Push queue

By default only one unit's PR is in flight at a time, so every other open PR doesn't sync and run CI again after each merge. That unit holds the *slot*, from the `go` that opens or updates its PR until the PR merges or closes, or the unit gives the slot up.

- A covered request from the slot holder gets `go` at once. A covered request from any other unit gets `go` if the slot is free and no other unit is first in the queue, and is queued otherwise. A request that is not covered gets `redo` at once, wherever the unit stands, so it can fix things while it waits.
- The queue serves units that already have an open PR and gave up the slot first, then everyone else, each in the order they asked.
- A held unit (see below) or one with an `--after` unit that has neither merged nor stopped never takes the slot: `go` and `queue` refuse a held unit, `go` refuses an `--after`-blocked one in serial mode, and the queue skips an `--after`-blocked unit at its head, so the next one is served. Giving the slot holder such an `--after` unit frees the slot, and `queue` on a slot holder that is `--after`-blocked gives up the slot and queues it. Nothing else makes a freed holder's child ask again, so once its `--after` units merge, `status` lists `<unit> can merge now its --after units merged: ask it to sync and push` until it asks.
- The holder gives up the slot (`yield`) when it reports `[blocked]` on something that does not block the merge, and when the watcher reports it `STUCK`. A `redo` to the holder frees the slot too. A question or escalated finding that blocks the merge gets `hold` instead, which also frees the slot. After either, the unit's PR stays open, and it goes to the front of the queue when it asks again; after an owner answer, it always asks again. A `go` the holder has not pushed yet stays outstanding through a `yield` or `hold`, so its old head is not reported ready while the approved push may still land; a `redo` or the unit's next request ends it.
- When the slot frees, the watcher reports `NEXT` for the first queued request. The manager checks it again and answers `go`; the child's sync on `go` keeps the waiting request from going stale.
- A request after the unit's last PR merged gets `redo <sha> pr-merged`, and the manager asks the owner whether it becomes a follow-up.
- Parallel mode (`set queue parallel`, only when the owner asks) answers every covered request with `go` at once. Requests still queued when it is turned on are answered again, one `go` each.

### Holds and ordering

A question the owner must answer before the PR can merge puts the unit on *hold* (`hold <unit> <reason>`). A held unit gets no `READY` or `REMIND` and, with the one exception below, no `STALLED`; it frees the slot if it holds it, loses its queue entry, and has every waiting request answered `redo <sha> held`. Its later requests are refused the same way (`go` and `queue` refuse it, naming the reason), so the child waits for the answer. Children mark each question they send "blocks merge: yes" or "blocks merge: no"; an escalated finding always blocks.

When the owner answers, the manager forwards the answer and holds the unit again as `answer sent: <topic>`, which ends at the child's next push request: the manager releases it (`release`) once no blocking question is left open and answers the request normally. That hold waits on the child, not the owner, so it is the one hold the watcher still reports `STALLED`: a child that never asks again is noticed, and the manager reminds it. A unit the owner designs in the child's own session is held as `own session: <topic>` while such a question is open. `release` restarts the stall clock and reports a ready PR `READY` again. `merged`, `closed` and `stop` clear a hold.

`--after U` on a unit says the unit must merge after `U`, without needing `U` merged to start (`--prereq` does that). `--after none` clears it. A unit cannot both need and merge after the same unit, and a cycle is refused. An `--after` unit that is stopped counts as satisfied. A unit that an open unit merges after, and every unit that one needs or merges after in turn (none through a stopped unit), may open past the WIP limit (in the same `open-ready` call), so the limit cannot deadlock the ordering.

`stop` ends a unit for good: its gate never opens again, it gives up the slot, its queued and unanswered requests are dropped, and its later requests get `redo <sha> unit-stopped`. A child that has already started never reads `gates.log` again, so the manager also tells it by message.

A WIP limit (`init --wip N` or `set wip N|none`) caps the units whose gate is open and that have not merged. Every unit is gated, so a lower limit holds back units that have not started; it never stops one that has. The manager's own `specs` unit is not counted.

## Merge bar

The manager merges a PR when all of these hold:

- The watcher reported `READY` (or `REMIND`) for its head, or `run.py check` printed `READY`.
- `branch-review:check`, run in the unit's worktree, says the review covers that head commit.
- The unit has no unanswered or queued request, and in serial mode the slot is free or the unit's own.
- The unit is not held, and every `--after` unit has merged or stopped (`status` shows neither `hold <unit>` nor `<unit> ... merges after`).
- The unit is not stopped. The watcher never reports `READY` or `REMIND` for a stopped unit.
- No approved push is outstanding for the unit (`status` does not list `push approved for <unit>`), so a stale head is never merged while the child pushes a newer one.
- In integration-branch mode: no integration sync is in flight (unit PRs), no late unit or ORPHAN PR is still out (the final PR), the PR targets the integration branch (unit PRs) or the base (the final PR), and the final PR is not one the owner merges.

The child need not be idle: it cannot push without a `go`, an outstanding `go` holds the merge, and the merge uses `gh pr merge --match-head-commit <sha>`.

## Integration-branch mode

- **Setup.** The base must be the repository's default branch, and the workflows' `pull_request` triggers must not exclude the integration branch; otherwise the manager stops and tells the owner. `init` refuses `--method rebase` with `--integration`: the final PR carries the integration child's base-merge commits, which a rebase merge flattens or refuses. The manager creates `integration/<slug>` on GitHub from the base's head and fetches it, and every child session starts from it. An open planning PR is retargeted to it. If the owner asks for the manager's planning commits (the spec or plan) to start the branch instead of a planning PR, it creates the branch by pushing them (`git push origin <commit>:refs/heads/integration/<slug>`); with feature review off it reviews them first, since nothing else would.
- **The integration child.** One extra child session owns the integration branch. Whenever the base moves, it records the unit PRs merged since its last sync with `branch-review:record`, merges the base, and asks to push like any child. A clean merge is covered by that record; a conflicted one gets a small merge-check review of the resolutions only. Before resolving a conflict it appends `<unit> syncing <sha> <branch>` to `requests.log`, which holds unit merges until its push request, so a unit cannot merge under the work; `wait` reports `STUCK <unit> - syncing` if no request follows within an hour, and a `redo` ends the hold. The manager answers a request that does not descend from the remote integration branch with `redo <sha> behind-integration`, and the child redoes its merge. Its pushes go straight to the integration branch, with no PR; the watcher reports `SYNCED` when one lands, and `run.py synced` then clears its `go`, frees the slot if it holds it, and forgets the `READY` reported for other units' PRs meanwhile, so they are reported `READY` again (`yield`, `merged` and `closed` on the integration unit also clear its `go`); and while one is unanswered or approved but not yet pushed, no unit PR merges (`status` lists `integration sync in flight`). Its requests are served before every other queued request. If a unit merged under its approved merge, it resets to the remote branch and redoes the merge rather than force-pushing. A `yield` of its slot spends its `go`, so unit merges stop waiting on a push that may never come.
- **`branch-review:record`.** A hidden branch-review skill that saves a round marking a commit already on `origin/<branch>` as recorded, without review. It is trusted because each unit PR passed the merge bar on its own branch, and it refuses anything not already pushed. Recorded rounds are listed as recorded in the review summary, never as reviews.
- **The final PR.** Once every other unit is done or stopped (at least one done), `open-ready` opens the final gate (`<unit> final review` or `<unit> final no-review` in `gates.log`). The integration child syncs once more, runs the whole-feature review if the owner asked for it, and opens the final PR into the base. With `owner`, the watcher and `check` report it `FINAL-READY`, never `READY`, and the manager tells the owner instead of merging. After it merges, the manager deletes the integration branch.
- **Late work.** Units, cards and ORPHAN PRs added during the run, and owner-approved fixes, all target the integration branch, before or after the final gate. The final PR waits until they land (`status` lists `final PR #<pr> waits on <units>`), an ORPHAN until the manager records it merged or closed; the integration child brings each late merge under review (feature review on) or records it (off).
- **Wrong base.** A unit PR that does not target the integration branch, or a final PR that does not target the base, is never reported ready; `status` lists it (`<unit> PR #<pr> targets <ref>, not <expected>`) and the manager tells its child to retarget it.
- **If every unit stops,** `status` says so and the manager asks the owner what to do with the integration branch.

In integration mode `status` adds `integration: <branch> into <base> · final merge: … · feature review: …`, and when they apply `integration sync in flight` (with `(resolving a conflicted merge of <base> since HH:MM)` after a `syncing` line; a line no `wait` has read yet counts too, here and in `check`), `final PR #<pr> waits on <units>` and `final PR #<pr> ready: the owner merges it`. Its "needs the manager" list adds `<unit> PR #<pr> targets <ref>, not <expected>: tell its child to retarget it`, `final gate for <unit> may open: run open-ready`, `integration push landed: run synced <unit> <sha>` (the watcher saw the push land but `synced` never ran) and `every unit stopped: ask the owner what to do with <branch>` (not once the integration unit is stopped or done).

## Run folder

Each run keeps its coordination files in the plugin's data folder, `${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/<run>/` (for a marketplace install, `~/.claude/plugins/data/implementation-manager-mbcrawfo-skills/<owner>-<repo>/<run>/`):

```
run.json      # the run's state: units, gates, PRs, the slot and the queue, the watcher's marks; written only by the script
requests.log  # <unit> push <sha> <branch>: appended by children; <unit> syncing <sha> <branch> by the integration child
pushes.log    # <unit> go <sha> | <unit> redo <sha> <reasons>: written only by the script
gates.log     # <unit> open | <unit> stop <reason> | <unit> final review|no-review: written only by the script
notes.md      # the units table, decisions, and what each merged unit left for later ones; written by the manager
```

Children poll `gates.log` and `pushes.log` and append to `requests.log` with `printf`, since the app stops delivering a session's messages after 10 sends without the user typing in it.

## Watcher events

`run.py wait` blocks until one event, prints it as one line and exits 0, also on error:

| Line | Meaning |
|---|---|
| `REQUEST <unit> <sha> <branch>` | A new request in `requests.log`. Every request is reported at once, whatever the queue, except one the manager already answered (`go`, `redo` or `queue` for that sha) before a wait read it. Malformed lines are skipped and listed by `status`. |
| `PENDING <unit> <sha> <branch>` | The request is still unanswered 15 minutes after its `REQUEST`; reported once per request. |
| `NEXT <unit> <sha>` | The slot is free and this queued request is first in line. |
| `READY <unit> <pr> <sha>` | Open, not draft, mergeable, at least one check and every check passed, no unresolved review thread; confirmed by a second poll 30 s later. Not reported while the unit has an unanswered or queued request, while it is pushing after a `go`, while it is held or an `--after` unit has neither merged nor stopped, or, in serial mode, while another unit holds the slot. |
| `REMIND <unit> <pr> <sha>` | Still ready and unmerged 30 minutes after `READY`. Not reported while the unit is held or an `--after` unit has neither merged nor stopped. |
| `MERGED <unit> <pr>`, `CLOSED <unit> <pr>` | Only for a PR the watcher saw open and the manager has not recorded. |
| `ORPHAN <unit> <pr>` | A PR opened on a done unit's branch after its last merge; the manager asks the owner whether to merge or close it. |
| `BEHIND <unit> <pr> <sha>` | The slot holder's PR (every open PR in parallel mode) is behind the base and the repository requires up-to-date branches. |
| `STUCK <unit> <pr> <reasons>` | The slot holder's PR has had failing checks, unresolved threads or a conflict for 20 minutes, with no new request and no `go` waiting to be pushed. |
| `STUCK <unit> - no-push` | The holder got `go` but no new head appeared within 30 minutes (the PR number, when it has one, takes the place of `-`). |
| `STUCK <unit> - syncing` | Integration mode: the integration child announced a conflicted base merge an hour ago and has not asked to push; unit merges wait on it. Reported once per announcement. |
| `STALLED <unit> <pr> head=<sha7><reasons>` | An open PR with nothing changed for the stall period (default 3 hours). Not reported while the unit is held, unless the hold's reason starts with `answer sent:`. |
| `ERROR <message>` | `gh` failed 10 PR polls in a row, or the run folder could not be read. |
| `SYNCED <unit> <sha>` | Integration mode: the integration branch's head equals the integration unit's outstanding `go`. The manager runs `run.py synced`. |
| `FINAL-READY <unit> <pr> <sha>` | Integration mode, `final merge: owner`: the final PR is ready, in place of `READY`, and gets no `REMIND`. The owner merges it, never the manager. |

Timings: `requests.log` is checked every 5 s and PRs are polled every 60 s. Each event is reported once, across restarts of `wait`.

## Tests

```bash
python3 -m unittest discover -s plugins/implementation-manager/tests -v
```
