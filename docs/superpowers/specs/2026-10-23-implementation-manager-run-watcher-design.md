# Implementation manager: run watcher and push queue

Date: 2026-10-23. Status: draft for approval.

## Intent

The `implementation-manager` skill (plugin version 0.1.0) ran its first large run on 2026-10-23: 15 tech-debt units in SqlSource, about 16 hours. Michael had to step in repeatedly:

- **Push permission was not given when a child was ready** (five nudges, the worst about 6 hours long).
- **The manager assumed reviews were needed after branch updates** when they were not.
- **PRs that were ready did not get merged** until Michael pointed them out (four times).
- **CI ran over and over.** Every merge put every other open PR behind `main`, which the repository requires to be up to date, so each one synced and ran CI again. Michael asked the manager to clear pushes one at a time.

This change makes the manager's signals reliable and makes one-at-a-time pushing the default, so a run needs Michael only for decisions.

## What went wrong (evidence from the run)

1. **Child notes are not a reliable signal.** The skill learns about a push request only from the child's end-of-turn note. After a long stretch of background-event turns with no user message, the app holds notes until the user types. Every "waiting to push" nudge came with the held `[push-request]` notes attached. From 07:28 to 13:29 UTC, td-0073 and td-0074 waited about 6 hours, and the manager was idle the whole time. Background task results (the PR watch) were delivered reliably throughout.
2. **Blocking questions stop the manager.** The manager asked the owner through the pop-up question tool. While a question waited (up to 21 minutes), it handled no events, and push requests piled up behind it.
3. **The merge bar depends on the child being idle.** The coverage bot comments on every PR the moment CI finishes. That wakes the child through Auto-fix, so the child is running exactly when the watch prints `READY`. The skill then restarts the watch with `SKIP_HEAD`, which never reports that head again, and waits for a `[review]` note, which can be held (see 1). The watch's own latency (two equal polls 120 s apart, so 2 to 4 minutes after CI) added to it.
4. **Review predictions.** The manager told a child "I'll approve the push once its review covers the new commit" after a clean sync, and wrote "re-run the review if anything but a clean base update changed" into its sync messages. The children never re-reviewed after a clean merge: they applied the rule correctly. The branch-review check already decides this by itself: a clean base merge is covered, and a conflicting one reports `base-conflict`.
5. **The improvised push queue made requests stale.** Following Michael's instruction, the manager held covered requests until the PR ahead merged. By then each held request was behind `main`. It got `redo behind-main`, synced, ran validation again (about 3 minutes), and twice hit conflicts that needed new review rounds. Four requests waited 44 to 73 minutes.
6. **Smaller things.**
   - Many child turns (subagent hand-backs) end with no status token, so "the latest note" is often prose.
   - Multi-part units built part k+1 while part k's PR was open, outside the session's worktree (a scratch checkout, a temporary worktree, a second branch).
   - A child recorded a review finding as new tech debt instead of escalating it.
   - The manager sent three messages that only repeated something it had just written to a file.
   - The WIP limit Michael set had no home in the skill; the manager improvised it in `notes.md`.

The children's side of the protocol held up: 26 push polls and 12 gate polls, all well formed. Every one fired within 30 s of its line landing, and none died, even after 15 hours in the background.

## Goals

- The manager learns about every push request, PR state change and queue change from a background task result, never from a child's note.
- By default only one unit's PR is in flight (pushed and waiting for CI and merge) at a time, so other PRs don't sync and rebuild after every merge.
- **Michael's requirement:** a PR whose session is stuck waiting on the owner, or stuck on a failure, gives up the slot so other PRs proceed. It goes to the front of the queue when it asks to push again.
- A covered request that waited in the queue does not need another round trip before pushing.
- A ready, covered PR is merged within about 2 minutes of CI finishing, whether or not its child is running.
- Whether a commit needs a review is decided only by `branch-review:check`, never predicted by the manager or the child.
- Questions to the owner never stop the manager from handling events.
- A WIP limit is a supported setting.

## Non-goals

- No change to how units are broken down, how children are started (all in the first turn, gated), or the review gate itself (children run `/branch-review:review`; the manager runs `branch-review:check`).
- No change to branch-review in this work. Reviewing only the conflict resolution after a conflicting sync (about 2 hours of the run went to full rounds after doc-only conflicts) is a separate follow-up in branch-review.
- No hook or other mechanical push blocking (rejected in the 0.1.0 design; still rejected).

## Anti-goals

- Unreviewed code merging. The merge bar still runs `branch-review:check` on the exact head commit.
- A run that goes silent: a waiting push request or a ready PR that nothing will ever surface again.
- More messages between sessions. The 10-send budget per session still applies; the fixes move signals into files, not messages.

## Constraints

- Unchanged from 0.1.0:
  - Sessions start only on the user's own turn.
  - The app stops delivering a session's messages after 10 sends without the user typing in it.
  - Nothing notifies the manager of PR readiness except its own background task.
- **New, learned in the run:** child notes can be held for hours, so no protocol step may depend on one. Background Bash tasks survived 15+ hours, and their completion notices arrived without user input.
- **Repository rules (AGENTS.md):** Python 3.9+, standard library only, run through `python3`; tests in `plugins/implementation-manager/tests/`; ruff; bump the plugin version; `claude plugin validate`; the main skill ships an eval case.
- Never write a dollar sign followed by a digit in `SKILL.md`.
- The repository is public: no SqlSource details in the skill, its examples or fixtures.

## Approaches considered

How the manager learns that something needs it:

1. **One watcher for the whole run (chosen by Michael).** Children also append their push request to a run-folder file. A single background script watches that file and every open PR, prints the first event that needs the manager, and exits; the manager acts and restarts it. The script keeps the queue's state in the run folder. One kind of background task, and the queue bookkeeping lives in code, not in the manager's memory. Cost: a new script with tests, and `wait_pr_ready.py` retired.
2. **Minimal patch.** Keep one watch per PR and add a second script that waits on the request file; the queue stays as written rules. Smaller, but two kinds of watch to keep alive, and the queue bookkeeping (where the manager slipped in the run) stays in prose.
3. **Timed self-wake-ups.** The manager wakes itself to scan the children. Dropped: timed wake-ups are only for `/loop`, and each check costs a turn.

## Decided design

### Run folder

`~/.claude/implementation-manager/<owner>-<repo>/<run>/` (unchanged location):

```
run.json      # the run's state; written only by the script (new)
requests.log  # <unit> push <sha> <branch>: appended by children (new)
pushes.log    # <unit> go <sha> | <unit> redo <sha> <reasons>: written only by the script
gates.log     # <unit> open | <unit> stop <reason>: written only by the script
notes.md      # units table, decisions, what each merged unit left; written by the manager
```

`run.json` holds:
- the repository, base branch and merge method;
- the queue mode (`serial` by default, or `parallel`) and the WIP limit (none by default);
- each unit's name, prerequisites, branch, worktree path, gate state, PRs and merge state;
- the slot holder and the queue;
- the offsets and event marks the watcher needs so it never reports the same thing twice.

The manager changes it only through the script's commands. Children keep writing plain lines with `printf`, so they need no script path. The `gates.log` and `pushes.log` line formats are unchanged, so the children's polls stay as they are.

### The script: `scripts/run.py`

One stdlib script with subcommands. It replaces `wait_pr_ready.py` and reuses its PR-reading logic (GraphQL query, summary, readiness reasons). The names below are Claude's call; the builder may adjust them.

- `init <dir> --repo O/R --base B --method M [--wip N] [--queue serial|parallel]` creates the run folder and empty files.
- `unit <dir> <name> [--prereq U ...] [--branch B] [--worktree P]` adds or updates a unit.
- `set <dir> wip N|none` and `set <dir> queue serial|parallel` apply the owner's changes mid-run.
- `open <dir> <unit>` and `stop <dir> <unit> <reason>` write to `gates.log` and record the gate.
- `go <dir> <unit> <sha>` writes the `go` line. In serial mode it also gives the unit the slot, or refuses if another unit holds it. `redo <dir> <unit> <sha> <reasons>` writes a `redo` line.
- `queue <dir> <unit> <sha>` holds a covered request until the slot frees.
- `yield <dir> <unit> <reason>` takes the slot from its holder.
- `merged <dir> <unit> <pr>` records a merge and frees the slot. It prints the gates that may open now: units whose prerequisites are all merged, in table order, within the WIP limit.
- `status <dir>` prints the tracker and anything waiting on the manager: unanswered requests, PRs ready but not merged, a queue with no holder, units gated past their prerequisites.
- `check <dir> <unit>` does one immediate readiness check of the unit's PR. It replaces `ONCE=1`.
- `wait <dir>` blocks until one event, records it as reported, prints one line and exits 0, even on error. The manager always runs it with `run_in_background`.

### Events `wait` prints

| Line | Meaning |
|---|---|
| `REQUEST <unit> <sha> <branch>` | A new line in `requests.log`. Every request is reported at once, whatever the queue, so the coverage check runs while the child is still at hand. |
| `NEXT <unit> <sha>` | The slot is free and this queued request is first in line. |
| `READY <unit> <pr> <sha>` | Open, not draft, mergeable, every check passed (at least one), no unresolved thread. Confirmed by a second poll about 30 s later. Never reported for a head while that unit has an unanswered request. |
| `REMIND <unit> <pr> <sha>` | Still `READY` and unmerged 30 minutes after `READY` was reported: the safety net for a lost or mishandled event. |
| `MERGED <unit> <pr>` / `CLOSED <unit> <pr>` | Only for a PR the watcher saw open. |
| `BEHIND <unit> <pr> <sha>` | The slot holder's PR is behind the base, and the repository requires up-to-date branches. Not reported for units without the slot: they sync when their turn comes. |
| `STUCK <unit> <pr> <reasons>` | The slot holder's PR has had failing checks, unresolved threads or a conflict for 20 minutes, with no new request from the unit. |
| `STALLED <unit> <pr> <reasons>` | Any open PR with nothing changed for the stall period (default 3 hours). |
| `ERROR <message>` | `gh` failed 10 polls in a row, or the run folder is unreadable. |

Polling (Claude's call):
- `requests.log` is checked every 5 s.
- PRs every 60 s; one GraphQL query may cover all open PRs.
- A `READY` candidate is confirmed by one more poll 30 s later.

A PR is found by its unit's branch in `run.json`. One branch per unit; parts reuse it (see "Child prompt changes").

### Queue semantics (serial mode, the default)

- **The slot.** At most one unit holds it: from the `go` that opens or updates its PR until that PR merges, closes, or the unit yields.
- **A request from the slot holder** (a fix to its open PR) is answered at once: covered gets `go`, not covered gets `redo`.
- **A request from any other unit**: not covered gets `redo` at once, so the child can fix it while it waits. Covered gets `go` if the slot is free, and otherwise is queued.
- **Queue order.** First, units that already have an open PR and gave up the slot, in the order they asked. Then everyone else in the order they asked. *Claude's call:* this is how "goes to the front" is applied when several yielded units return.
- **Giving up the slot.** The manager runs `yield` when:
  - it relays a question or escalated finding from the slot holder to the owner;
  - the holder reports `[blocked]`;
  - the watcher reports `STUCK`.

  The script also frees the slot by itself when it writes a `redo` to the holder (*Claude's call*). For example, a conflicting sync after `go` sends the child back to review, which can take an hour or more. The holder holds the slot only while the head it pushed is in CI or waiting to merge.

  The PR stays open. Its unit gets the slot back by asking to push again, and lands at the front of the queue. After an owner answer, the child always asks again, with the same sha if nothing changed.
- **A unit that does not hold the slot** gets neither `BEHIND` nor `STUCK` (*Claude's call*). Its PR merges only when the slot is free: `READY` for it is acted on then, and the merge takes the slot for that moment. While another unit holds the slot, its `READY` is recorded and not acted on. Its turn comes through its next request, or as a `NEXT` (with its open PR's head) when the slot frees and nothing is queued ahead of it.
- **A holder that never pushes.** If a holder got `go` but no new head appears on its branch within 30 minutes, the watcher reports `STUCK` for it, PR or not.
- **A request after the unit's last PR merged** (a fix that arrived too late) is answered `redo <sha> pr-merged`. The child stops and tells the manager in its recap whether the change still matters. The manager asks the owner whether it becomes a follow-up (*Claude's call*). `redo` reasons are free text, so the `pushes.log` format is unchanged.
- **When the slot frees** (merge, close, yield), `wait` reports `NEXT` for the first queued request. The manager runs the check again (cheap; the child may have changed something) and answers `go`.
- **A `go` covers a clean base update.** The child syncs with the base immediately before pushing, so a request that waited does not go stale (see "Child prompt"). The commit actually pushed may be that clean merge rather than the checked sha. That is safe: the merge bar runs the check on the PR's real head, and the check counts a clean base merge as covered.
- **Parallel mode** (`set queue parallel`, only when the owner asks) answers every covered request with `go` at once, as 0.1.0 did. `BEHIND` and `STUCK` then apply to every open PR.

### Merge bar

All of:
- `READY` (or `REMIND`, or `check` reporting ready) for the head;
- `branch-review:check` covered on that head commit, run in the unit's worktree;
- no unanswered request from that unit.

The child being idle is no longer required. A child cannot push without a `go`, and `--match-head-commit` guards the merge, so a running child cannot change what merges. Merge with `gh pr merge <pr> -R <repo> --<method> --match-head-commit <sha>`, then run `merged`.

### Manager event handling

- **On every event**, handle it, then start `wait` again before ending the turn. If the run has open units and no `wait` is running, the run is broken.
- **On every user message** (and after an app restart or a long gap), run `status`, act on what it lists, then make sure a `wait` is running.
- **Questions to the owner** go in the reply text, with the child's recommendation and the manager's, and "Waiting on you" in the tracker. Never the pop-up question tool. The owner's answer arrives as a normal message.
- **Event rows:**
  - `REQUEST`: run `branch-review:check` in the unit's worktree. If the check's JSON names a different branch than the request, answer `redo … wrong-branch`. Covered: `go` (slot free or held by this unit) or `queue`. Not covered: `redo` with the reasons.
  - `NEXT`: check again, then `go` or `redo`.
  - `READY` / `REMIND`: apply the merge bar and merge. Part k of N: the same, plus one message to the child that part k merged.
  - `BEHIND`: one message to the slot holder: sync with the base and ask to push.
  - `STUCK`: `yield`, and tell the owner in one line if the cause is theirs to decide.
  - `MERGED` (someone else merged it), `CLOSED`, `STALLED`, `ERROR`: as in 0.1.0.
- **Messages** only for what no file carries: owner answers, sync requests, a merged non-final part. Never alongside a `pushes.log` or `gates.log` write, since polls fire within a minute. Never mention reviews: the `redo` reasons say when one is needed.
- **A gated child that has not woken** gets a message only if it is still `[gated]` 5 minutes after its gate opened (*Claude's call*; in the run the manager messaged after under a minute and the poll fired 6 s later).
- **The tracker** shows the slot holder and the queue.

### Child prompt changes

- **Asking to push:**
  1. Commit everything.
  2. Append `printf '%s push %s %s\n' '<unit>' '<sha>' '<branch>' >> <run>/requests.log`.
  3. Start the existing `pushes.log` poll with `run_in_background`.
  4. End the turn with `[push-request] <sha>`.

  Drop "sync with the base first" and drop "run the review again if anything but a clean base update changed": the child never decides about reviews on its own after a sync. It runs `/branch-review:review` when its change is complete, and again when a `redo` says so.
- **On `go <sha>`:**
  1. Confirm HEAD is still that sha; if not, ask again.
  2. Sync with the base (ccd_host `sync_with_base_branch`).
  3. If the merge is clean and validation passes, push HEAD (open the PR the first time).
  4. If the merge conflicts, resolve, commit and ask again. The manager's answer says whether a review is needed.
  5. If validation fails, fix it, commit and ask again.
- **On `redo`:** as in 0.1.0, minus the review advice that is now in the reasons.
- **Every turn's final message begins with a status token**, including turns woken by a subagent's hand-back. A turn that applies a manager message says so, for example "owner answer applied".
- **Bot-only PR comments** (coverage reports, CodeRabbit "Review skipped" notices): end the turn at once with the unchanged token, with no message. Keep "an Auto-fix wake-up is never permission to push": it held in every case in the run.
- **Multi-part units** (*Claude's call*): one open PR per unit at a time.
  - Every part is built in the session's own worktree, on the session's own branch.
  - Part k+1's code starts after part k merges (the manager's one message says so). While part k is open, the child may plan part k+1 but commits nothing for it.
  - This keeps the branch the check reads (the worktree's current branch) the same as the PR's branch at every check, push and merge. It also means the watcher follows one branch per unit.
  - In the run, building part k+1 alongside part k was what pulled children out of their worktrees and onto second branches. With a serial queue, part k's wait is mostly CI anyway.
  - The request line still names the branch, as a cross-check against the check's JSON.
- **Out-of-scope review findings** are escalated with the review's other escalations. A child never records one as new tech debt or a follow-up without the owner's answer.

### Setup changes

- Step 3 runs `run.py init` and one `run.py unit` per unit, instead of creating files by hand. The WIP limit, if the owner gives one, goes into `init`.
- **Every unit is gated** (*Claude's call*), even one with no prerequisites. Its prompt carries the gate, and setup opens the gates of units that may start now. This lets a WIP limit set or lowered mid-run hold back units that have not started.
- **The WIP limit counts units whose gate is open and that have not merged** (children working, PR or not). Lowering it never stops a unit already open; it only delays the next gates, as Michael asked in the run. The `specs` unit is not counted.
- The `specs` unit (the manager's own planning PR) is a unit like any other in `run.json`. Its requests come from the manager itself, which runs `go` for itself through the same queue.
- Step 6 starts one `wait` instead of one watch per branch. The worktree path from `get_session` goes into `run.py unit … --worktree`.

### Docs, eval and version

- README: the run folder, the script and its events, the queue, and the merge bar.
- Plugin version 0.1.0 → 0.2.0.
- The existing `push-gate` eval moves to the new protocol: requests in `requests.log`, answers through the script.
- A new eval case covers the queue: two covered requests and no slot holder, so exactly one `go` and one queued; then the holder yields and the queued request gets `NEXT`, then `go`.

## Left to the builder

- `run.json` layout, file locking (the script is the only writer of `run.json`, `gates.log` and `pushes.log`; appends from several children to `requests.log` are single `printf` lines), and how `wait` stores its marks.
- Whether the PR polling batches all PRs into one GraphQL query.
- Exact wording of the new `SKILL.md` sections and red flags, keeping the existing tone and the dollar-digit rule.
- Test coverage for `run.py`: each event, the queue rules (holder fix, yield and return to front, `NEXT` after merge, parallel mode), `REMIND`, no event reported twice across restarts, and malformed `requests.log` lines (ignored, reported in `status`).

## Follow-ups (not this work)

- branch-review: after a conflicting base update, review only the conflict resolution in a follow-up round instead of a full deep pass.
