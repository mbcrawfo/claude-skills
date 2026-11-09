# The merge bar

All of:
- `READY` or `REMIND` for that PR and head, or `run.py check <run> <unit>` printing `READY <pr> <sha>`;
- covered on that head: the `verdict` line `wait --full` printed with a `READY` or `REMIND` event counts only when it is a JSON verdict for the same sha as that event's head sha (SKILL.md, the verdict rule); otherwise `branch-review:check --repo <worktree> --commit <sha>` covered, `<worktree>` the unit's worktree;
- no unanswered or queued request from that unit, and in serial mode the slot is free or held by that unit (`run.py status <run>` shows both);
- the unit is not stopped (`run.py status <run>` does not list it `stopped`);
- the unit is not held and every `--after` unit has merged (`run.py status <run>` shows neither `hold <unit>` nor `<unit> … merges after`);
- no approved push is outstanding for that unit (`run.py status <run>` does not list `push approved for <unit>`). The child is about to push a newer commit, so wait for it; a `READY` for its new head follows.
- the run is not in integration mode, or `run.py status <run>` lists no `integration sync in flight` for a unit PR, no `final PR … waits on` for the final PR, and the final PR is not owner-merged (`final merge: owner` in the `integration:` line).
- the run is not in integration mode, or the PR targets `integration/<slug>` (a unit PR) or `<base>` (the final PR): `run.py status <run>` lists no `<unit> PR #<pr> targets …` for it.

The child being idle is not required: it cannot push without a `go`, an outstanding `go` holds the merge, and `--match-head-commit` guards it.

Not met because the check (or the same-sha verdict) says not covered: send the child one message: its PR head `<sha7>` is not covered (`<reasons>`); fix that and ask to push. Not met because the check errors or the unit has no worktree: file a decision item for the unit, unless that cause for it is already an open item (`run.py item list <run>`). An unanswered request: answer it first. A queued request, another unit's slot, a hold or an unmerged `--after` unit: do nothing; the unit's turn comes through `NEXT`, its next request or the watcher's next `READY`. An integration sync in flight or a final PR that waits on units: do nothing; the watcher reports `READY` or `REMIND` once it clears. An owner-merged final PR: never merge it. A PR on the wrong base: send its child one message to retarget it, as the `targets` line says.

## Merge

`gh pr merge <pr> -R <repo> --<method> --match-head-commit <sha> && gh pr view <pr> -R <repo> --json state --jq .state`, which prints `MERGED`. If it fails because the head moved, do nothing more: the watcher reports the new head. For anything else (approval required, conflicts, branch protection), file a decision item for the unit with the error, unless that cause for it is already an open item (`run.py item list <run>`).

## After a merge

1. If the PR's title says `(part k of N)` with k < N: `run.py merged <run> <unit> <pr> --more`, send the child one message that part k merged and it may start part k+1, and skip to step 4.
2. `run.py merged <run> <unit> <pr>`.
3. Save the PR description with `mkdir -p <run>/prs && gh pr view <pr> -R <repo> --json body --jq .body > <run>/prs/<unit>-<pr>.md`, and read from that file only what `notes.md` needs (and anything the child messaged you about it). Read the app's shared notes folder for files the merged unit wrote (`<unit>-notes.md`). Its path is at the top of `notes.md` (setup.md, step 3). If it is not there yet: the app adds it to each child's prompt, so look among your own additional working directories, or else in one child's first prompt (`list_events`, paging back with `before_uuid` to the child's first message), and write it there. Append a section for the unit to `notes.md`: its PRs, and what later units must know that the specs don't say (owner decisions, changed names or ids, new mechanisms, limits found), from either source. `notes.md` stays the one record you keep. Then `run.py open-ready <run>`.
4. Run `run.py status <run>`. Act first on any `<unit> can merge now its --after units merged` line it lists (the "needs the manager" list in events.md). Then reply with the tracker. Send no other message to the merged unit's child.

## Red flags

| Thought | Reality |
|---|---|
| "Part 1 merged, open the dependents' gates" | Not until part N: `merged --more` keeps the unit open. |
| "I'll let the child know its PR merged" | It doesn't need to know, except a non-final part. Every message spends the 10-send budget. |
