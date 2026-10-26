# implementation-manager

Runs a planned multi-phase piece of work to merged code. The session you run it in becomes the manager: it starts one child session per unit of work (a phase or sub-phase), each delivering a pull request, holds back units whose prerequisites have not merged, and merges each PR when it is ready. Every unit is reviewed with branch-review, and the manager checks branch-review's state itself before letting a child open its PR or push again, and before merging.

## Requirements

- The [`branch-review`](../branch-review/README.md) plugin, for `/branch-review:review` and `branch-review:check`. It is a declared dependency, so installing this plugin installs it too. The manager stops at setup if either skill is missing.
- The Claude desktop app's session tools (`start_session`, `get_session`, `list_events`, `sync_with_base_branch`, the ccd_pr tools), which start and inspect child sessions. The skill does not work in a plain terminal session.
- `gh`, logged in, and `python3` (3.9+).

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install implementation-manager@mbcrawfo-skills
```

## Run it

Ask a session to be the implementation manager, either right after planning the work there or with the spec, plan or epic to run:

```
/implementation-manager:implementation-manager [spec, plan or epic path]
```

Invoking it authorizes starting the child sessions and merging the run's PRs once they meet the merge bar (below). It authorizes nothing else.

Pushes go through the manager. A child reviews its change with `/branch-review:review` until it converges, then asks to push by appending `<unit> push <sha> <branch>` to the run's `requests.log` and ending its turn with `[push-request] <sha>`. The run watcher reports the request; the manager runs `branch-review:check` on that commit in the child's worktree and answers through the run script, which appends `<unit> go <sha>`, or `<unit> redo <sha> <reasons>` with the check's reasons joined by `, `, to `pushes.log`. The child's poll reads only lines added after it asked, so it can ask again with the same sha. An Auto-fix wake-up in a child (CI failure, review comment, merge conflict) is never permission to push.

On `go`, the child syncs with the base right before it pushes. A clean merge is pushed after validation, with no new request; the review check counts a clean base merge as covered. A conflicting merge, or a failed validation, sends the child back to ask again; for a conflicting merge the check reports `base-conflict`, and the child's review checks the resolutions in a small merge-check round.

The child's PR description carries the review summary's Escalated, Deferred, Invalid, Outside the change and Intent check sections under "Review notes". Findings outside the change are not deferrals: where the repository forbids deferring review findings, the review escalates them, and the check reports `escalated` until the owner answers. The child prompt's review command has no `--no-second-opinion`, so a second opinion the review calls for always runs; until it does, the check reports `second-opinion-pending`.

Child notes can be held by the app for hours, so nothing in the protocol waits on one: requests are lines in a file, and the manager learns everything from the run watcher.

## Push queue

By default only one unit's PR is in flight at a time, so every other open PR doesn't sync and run CI again after each merge. That unit holds the *slot*, from the `go` that opens or updates its PR until the PR merges or closes, or the unit gives the slot up.

- A covered request from the slot holder gets `go` at once. A covered request from any other unit gets `go` if the slot is free and no other unit is first in the queue, and is queued otherwise. A request that is not covered gets `redo` at once, wherever the unit stands, so it can fix things while it waits.
- The queue serves units that already have an open PR and gave up the slot first, then everyone else, each in the order they asked.
- A held unit (see below) or one whose `--after` units have not all merged never takes the slot: `go` and `queue` refuse a held unit, `go` refuses an `--after`-blocked one in serial mode, and the queue skips an `--after`-blocked unit at its head, so the next one is served.
- The holder gives up the slot (`yield`) when it reports `[blocked]` on something that does not block the merge, and when the watcher reports it `STUCK`. A `redo` to the holder frees the slot too. A question or escalated finding that blocks the merge gets `hold` instead, which also frees the slot. After either, the unit's PR stays open, and it goes to the front of the queue when it asks again; after an owner answer, it always asks again. A `go` the holder has not pushed yet stays outstanding through a `yield` or `hold`, so its old head is not reported ready while the approved push may still land; a `redo` or the unit's next request ends it.
- When the slot frees, the watcher reports `NEXT` for the first queued request. The manager checks it again and answers `go`; the child's sync on `go` keeps the waiting request from going stale.
- A request after the unit's last PR merged gets `redo <sha> pr-merged`, and the manager asks the owner whether it becomes a follow-up.
- Parallel mode (`set queue parallel`, only when the owner asks) answers every covered request with `go` at once. Requests still queued when it is turned on are answered again, one `go` each.

### Holds and ordering

A question the owner must answer before the PR can merge puts the unit on *hold* (`hold <unit> <reason>`). A held unit gets no `READY`, `REMIND` or `STALLED`, frees the slot if it holds it, loses its queue entry, and has every waiting request answered `redo <sha> held`. Its later requests are refused the same way (`go` and `queue` refuse it, naming the reason), so the child waits for the answer. Children mark each question they send "blocks merge: yes" or "blocks merge: no"; an escalated finding always blocks. When the owner answers, the manager forwards the answer and holds the unit again as `answer sent: <topic>`, which ends at the child's next push request: the manager releases it (`release`) once no blocking question is left open and answers the request normally. `merged`, `closed` and `stop` clear a hold.

`--after U` on a unit says the unit must merge after `U`, without needing `U` merged to start (`--prereq` does that). `--after none` clears it. A unit cannot both need and merge after the same unit, and a cycle is refused. An `--after` unit that is stopped counts as satisfied. A unit that an open unit merges after may open past the WIP limit, so the limit cannot deadlock the ordering.

A WIP limit (`init --wip N` or `set wip N|none`) caps the units whose gate is open and that have not merged. Every unit is gated, so a lower limit holds back units that have not started; it never stops one that has. The manager's own `specs` unit is not counted.

## Merge bar

The manager merges a PR when all of these hold:

- The watcher reported `READY` (or `REMIND`) for its head, or `run.py check` printed `READY`.
- `branch-review:check`, run in the unit's worktree, says the review covers that head commit.
- The unit has no unanswered or queued request, and in serial mode the slot is free or the unit's own.
- The unit is not held, and every `--after` unit has merged (`status` shows neither `hold <unit>` nor `<unit> ... merges after`).
- The unit is not stopped. The watcher never reports `READY` or `REMIND` for a stopped unit.
- No approved push is outstanding for the unit (`status` does not list `push approved for <unit>`), so a stale head is never merged while the child pushes a newer one.

The child need not be idle: it cannot push without a `go`, an outstanding `go` holds the merge, and the merge uses `gh pr merge --match-head-commit <sha>`.

## Run folder

Each run keeps its coordination files in the plugin's data folder, `${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/<run>/` (for a marketplace install, `~/.claude/plugins/data/implementation-manager-mbcrawfo-skills/<owner>-<repo>/<run>/`). Runs from 0.1.0 used `~/.claude/implementation-manager/`; nothing reads that folder any more, so delete it once no run there is still going:

```
run.json      # the run's state: units, gates, PRs, the slot and the queue, the watcher's marks; written only by the script
requests.log  # <unit> push <sha> <branch>: appended by children
pushes.log    # <unit> go <sha> | <unit> redo <sha> <reasons>: written only by the script
gates.log     # <unit> open | <unit> stop <reason>: written only by the script
notes.md      # the units table, decisions, and what each merged unit left for later ones; written by the manager
```

Children poll `gates.log` and `pushes.log` and append to `requests.log` with `printf`, since the app stops delivering a session's messages after 10 sends without the user typing in it.

## Run script

```
python3 scripts/run.py <command> <run folder> [args]
```

| Command | Does |
|---|---|
| `init <dir> --repo O/R --base B --method merge\|squash\|rebase [--wip N] [--queue serial\|parallel]` | Creates the run folder and its files. |
| `unit <dir> <name> [--prereq U ...] [--after U ... \| --after none] [--title T] [--branch B] [--worktree P] [--specs]` | Adds a unit, or updates the fields given. `--after` units must merge first but do not gate the start; `--after none` clears them. `--title` is a label shown as `name (title)` in `status`. Names use letters, digits, `-` and `_`, starting with a letter or digit. |
| `set <dir> wip <N\|none>`, `set <dir> queue <serial\|parallel>` | Changes the WIP limit or the queue mode. In parallel mode it prints `queued <unit> <sha>` for each request still queued, which the manager answers with a fresh check and `go`. |
| `open <dir> <unit>`, `open-ready <dir>` | Opens one gate, or every gate that may open now (prerequisites merged, within the WIP limit), printing one name per line. |
| `stop <dir> <unit> <reason...>` | Stops a unit for good: its gate never opens again, it gives up the slot, its queued and unanswered requests are dropped, and `go` and `queue` refuse it (its later requests get `redo <sha> unit-stopped`). A child that has already started never reads `gates.log` again, so the manager also tells it by message. |
| `go <dir> <unit> <sha>` | Allows the push. In serial mode it takes the slot, and refuses while another unit holds it or, with the slot free, another unit is first in the queue. |
| `redo <dir> <unit> <sha> <reasons...>` | Refuses the push with one line of reasons. |
| `queue <dir> <unit> <sha>` | Holds a covered request until the slot frees. |
| `yield <dir> <unit> <reason...>` | The slot holder gives up the slot. |
| `hold <dir> <unit> <reason...>` | Holds the unit's merge until `release`, `merged`, `closed` or `stop`. It frees the slot, drops the queue entry and answers each waiting request `redo <sha> held`; `go` and `queue` refuse the unit (`unit 'X' is held: <reason>; answer its push requests with: redo <sha> held`). |
| `release <dir> <unit>` | Lifts the hold and restarts the stall clock. |
| `merged <dir> <unit> <pr> [--more]` | Records a merge and frees the slot; `--more` keeps the unit open for its next part. Prints the gates that may open now. |
| `closed <dir> <unit> <pr>` | Records a PR closed without merging and frees the slot. |
| `status <dir>` | The slot, the queue, merged, open and gated units (with `merges after`, `hold <unit>: <reason> since HH:MM` and `name (title)` labels), and everything waiting on the manager, including a `go` not pushed yet and a slot holder whose PR is behind the base. |
| `clear-bad <dir>` | Forgets the malformed request lines `status` lists, and prints how many. |
| `check <dir> <unit>` | Checks the unit's PR now: `READY <pr> <sha>` (two equal clean polls 30 s apart), `NOT-READY <pr> head=<sha7><reasons>`, `NOT-READY - no open PR from <branch>`, `MERGED <pr>`, `CLOSED <pr>` or `ERROR <message>`. |
| `wait <dir> [--stall-hours N]` | Blocks until one event, prints it as one line and exits 0, also on error. |

It exits 0 on success, 1 when a command is refused (one line on stderr, nothing changed) and 2 on a usage error.

`wait` prints one of:

| Line | Meaning |
|---|---|
| `REQUEST <unit> <sha> <branch>` | A new request in `requests.log`. Every request is reported at once, whatever the queue, except one the manager already answered (`go`, `redo` or `queue` for that sha) before a wait read it. Malformed lines are skipped and listed by `status`. |
| `PENDING <unit> <sha> <branch>` | The request is still unanswered 15 minutes after its `REQUEST`; reported once per request. |
| `NEXT <unit> <sha>` | The slot is free and this queued request is first in line. |
| `READY <unit> <pr> <sha>` | Open, not draft, mergeable, at least one check and every check passed, no unresolved review thread; confirmed by a second poll 30 s later. Not reported while the unit has an unanswered or queued request, while it is pushing after a `go`, while it is held or an `--after` unit is unmerged, or, in serial mode, while another unit holds the slot. |
| `REMIND <unit> <pr> <sha>` | Still ready and unmerged 30 minutes after `READY`. Not reported while the unit is held or an `--after` unit is unmerged. |
| `MERGED <unit> <pr>`, `CLOSED <unit> <pr>` | Only for a PR the watcher saw open and the manager has not recorded. |
| `ORPHAN <unit> <pr>` | A PR opened on a done unit's branch after its last merge; the manager asks the owner whether to merge or close it. |
| `BEHIND <unit> <pr> <sha>` | The slot holder's PR (every open PR in parallel mode) is behind the base and the repository requires up-to-date branches. |
| `STUCK <unit> <pr> <reasons>` | The slot holder's PR has had failing checks, unresolved threads or a conflict for 20 minutes, with no new request and no `go` waiting to be pushed. |
| `STUCK <unit> - no-push` | The holder got `go` but no new head appeared within 30 minutes (the PR number, when it has one, takes the place of `-`). |
| `STALLED <unit> <pr> head=<sha7><reasons>` | An open PR with nothing changed for the stall period (default 3 hours). Not reported while the unit is held. |
| `ERROR <message>` | `gh` failed 10 PR polls in a row, or the run folder could not be read. |

Timings: `requests.log` is checked every 5 s and PRs are polled every 60 s. Each event is reported once, across restarts of `wait`.

## Tests

```bash
python3 -m unittest discover -s plugins/implementation-manager/tests -v
```
