# implementation-manager

Runs a planned multi-phase piece of work to merged code. The session you run it in becomes the manager: it starts one child session per unit of work (a phase or sub-phase), each delivering a pull request, holds back units whose prerequisites have not merged, and merges each PR when it is ready. Every unit is reviewed with branch-review, and the manager checks branch-review's state itself before letting a child open its PR or push again, and before merging.

## Requirements

- The [`branch-review`](../branch-review/README.md) plugin, for `/branch-review:review` and `branch-review:check`. The manager stops at setup if either is missing.
- The Claude desktop app's session tools (`start_session`, `get_session`, `list_events`, `sync_with_base_branch`, the ccd_pr tools), which start and inspect child sessions. The skill does not work in a plain terminal session.
- `gh`, logged in, and `python3` (3.9+).

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install branch-review@mbcrawfo-skills
/plugin install implementation-manager@mbcrawfo-skills
```

## Run it

Ask a session to be the implementation manager, either right after planning the work there or with the spec, plan or epic to run:

```
/implementation-manager:implementation-manager [spec, plan or epic path]
```

Invoking it authorizes starting the child sessions and merging the run's PRs once they meet the merge bar: the watch reports the PR ready, the child is idle with nothing left to do, and `branch-review:check` says the review covers the PR's head commit.

Pushes go through the manager. A child reviews its change with `/branch-review:review` until it converges, then asks to push by ending its turn with `[push-request] <sha>`. The manager runs `branch-review:check` on that commit in the child's worktree and appends `<unit> go <sha>`, or `<unit> redo <sha> <reasons>` with the check's reasons joined by `, `, to the run's `pushes.log`. The child's poll reads only lines added after it starts, so it can ask again with the same sha. An Auto-fix wake-up in a child (CI failure, review comment, merge conflict) is never permission to push.

## Run folder

Each run keeps its coordination files in `~/.claude/implementation-manager/<owner>-<repo>/<run>/`:

```
gates.log    # <unit> open | <unit> stop <reason>: when a gated child may start
pushes.log   # <unit> go <sha> | <unit> redo <sha> <reasons>: push permission per commit
notes.md     # the units table, and what each merged unit left for later ones
```

Only the manager writes `gates.log` and `pushes.log`. Children poll them, since the app stops delivering a session's messages after 10 sends without the user typing in it.

## Watch script

Nothing notifies the manager when a PR is ready or merged, so it runs one watch per open unit in the background:

```
python3 scripts/wait_pr_ready.py <owner/repo> <branch> [stall-hours, default 3]
```

It prints one line and exits:

- `READY <pr> <sha>`: open, not draft, mergeable, at least one check and every check passed, no unresolved review thread, the same on two polls in a row.
- `ATTENTION <pr> <sha> behind-base`: the repo requires up-to-date branches and this one is behind.
- `MERGED <pr>` or `CLOSED <pr>`: only for the PR this watch saw open.
- `STALLED <pr> head=<sha7><reasons>`: nothing changed for the stall period.
- `ERROR <message>`: `gh` failed 10 polls in a row.

Environment:

- `POLL_SECONDS`: seconds between polls, default 120.
- `SKIP_HEAD=<sha>`: never report `READY` or `ATTENTION` for a head starting with this.
- `ONCE=1`: check now (two polls 30 s apart), print `READY` or `NOT-READY <pr> head=<sha7><reasons>` (or `NOT-READY - no open PR from <branch>`), and exit.

## Tests

```bash
python3 -m unittest discover -s plugins/implementation-manager/tests -v
```
