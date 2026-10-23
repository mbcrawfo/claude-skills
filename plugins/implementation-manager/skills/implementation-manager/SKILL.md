---
name: implementation-manager
description: Use when the user asks this session to be the implementation manager, or to run a planned multi-phase or multi-sub-phase piece of work to completion through child sessions that each deliver a pull request, either in a dedicated session or right after planning the work here.
argument-hint: "[spec, plan or epic path]"
---

# Implementation Manager

<!-- Editing note: never write a dollar sign followed by a digit in this file. Claude Code replaces it with a skill argument when the skill loads. -->

This session takes planned work from spec to merged code. It starts one child session per unit of work (a phase or sub-phase), clears each child's pushes against branch-review's state, merges each child's pull request when it is ready, and releases the units that depend on it. It writes no product code. Its own planning PR, if one is open, is the one PR it works on itself: it keeps Auto-fix on for it, fixes every finding (thread or review body), reviews it with `/branch-review:review` and gates each push with `branch-review:check`, like a child's. The user is needed only for questions and problems.

**Invoking this skill authorizes** starting the child sessions and merging the run's PRs (the children's, cards the user starts from this run, and this session's own planning PR) once they meet the merge bar. It authorizes nothing else.

Arguments: `$ARGUMENTS`

## Three constraints that shape everything

1. **Sessions can only be started on the user's own turn.** `start_session` with `"user_asked"` is refused on a turn started by a background task, a child's note or another session's message; it degrades to a card the user must click. So **start every child in the first turn**, and hold back the ones with unmerged prerequisites with a *gate* (a file the child polls), not by starting them later.
2. **Messages are a scarce budget.** The app stops delivering a session's messages after 10 sends without the user typing in it. Coordinate through files in the run folder (gates, push permissions), never through status messages.
3. **Nothing notifies you when a child's PR is ready or merged.** A passing check sends no event, and Auto-fix in a child wakes the child, not you. The watch, `python3 ${CLAUDE_SKILL_DIR}/../../scripts/wait_pr_ready.py` (read its header for its outputs), is your only signal for PR readiness; run one per open unit at all times.

## Setup (first turn)

1. **The work breakdown.** Use this conversation's plan if it has one; otherwise read the spec, plan or epic named in the arguments. List the units: name (short, e.g. `5-3`), title, spec path, prerequisites, and a `branch_name` you choose. Take order and dependencies from the docs; units the docs allow side by side run in parallel. If this session has its own open PR the units need (e.g. the specs), it is a unit named `specs` with no child, and every unit depends on it, so even the first unit is gated.
2. **Repository facts and review tools:** `owner/repo`, base branch, and merge method (the repository's instructions; otherwise `gh repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed`, and if more than one is allowed and nothing says which, ask). Confirm `branch-review:check` and `branch-review:review` are among your available skills. If either is missing, stop and tell the user to install branch-review (`/plugin install branch-review@mbcrawfo-skills`).
3. **The run folder:** `~/.claude/implementation-manager/<owner>-<repo>/<run>/`, `<run>` a slug of the work (e.g. `query-gen-phase-5`). Create `gates.log` and `pushes.log` (both empty), and `notes.md` holding the units table (name, title, spec, prerequisites, session, branch, PRs). Only you write `gates.log` and `pushes.log`.
4. **Go or ask.** If the breakdown and dependencies are stated in the docs or were agreed in this conversation, continue. If you inferred any dependency, or anything in steps 1–2 is ambiguous, show the units table and ask; start everything on the user's answer.
5. **Start every child now** (see "Starting children"). If any start comes back as a card instead, say so and list it under "Waiting on you" in the tracker.
6. **Watch.** `get_session` on each child for its actual branch (the app adds a prefix and suffix; if it is not set yet, check again next turn) and fill in the table. Start `python3 ${CLAUDE_SKILL_DIR}/../../scripts/wait_pr_ready.py <owner/repo> <branch>` with `run_in_background` for every child branch, and for this session's branch if there is a `specs` unit.
7. Open the gate of every unit whose prerequisites are all merged (see "After a merge") and reply with the tracker.

## Starting children

If this session already did other work, call `mcp__ccd_session__mark_chapter` first. Write a bold title line and one sentence for each session right before its call. Each call: `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, `name`, `title` and `branch_name` from the unit. `get_session` with `"self"` gives your own id for the prompt.

The child prompt has these parts, in this order:

1. **Goal:** implement `<unit title>` as specified in `<spec path>`, delivered as one PR to `<base>` on `<owner/repo>`. If the repository's PR size rule forces several, they land in order, each from this session's worktree, each titled with `(part k of N)`.
2. **Gate** (only for a unit with prerequisites): "The work you build on is not merged yet. Run this with Bash `run_in_background`, then end your turn with `[gated]`; it prints when you may start: `f=<run>/gates.log; until l=$(grep -E '^<name> (open|stop)' "$f" 2>/dev/null | tail -1) && [ -n "$l" ]; do sleep 60; done; echo "$l"`. On `open`, continue with the next step. On `stop`, do nothing and end your turn quoting the line." Use the absolute run-folder path.
3. **Start:** bring the branch up to date with `<base>` (ccd_host `sync_with_base_branch`); confirm the prerequisites' code is in the tree, and stop and say so if not; read `<run>/notes.md`, which records what earlier units actually merged.
4. **Read:** the repository's agent instructions (root and every folder touched), the spec, and the parts of the epic or parent docs it names. Repository rules win over any skill's defaults. Where merged code differs from the spec's assumptions, follow the code and say so in the plan.
5. **Plan and build:** write the plan with superpowers:writing-plans where the repository keeps plans, then implement it, preferably with superpowers:subagent-driven-development, validating every commit and push the way the repository requires.
6. **Unit-specific instructions:** the owner's decisions and constraints for this unit; for a parallel sibling, which other unit is running, which shared files you both touch, and to keep those changes small.
7. **Pull request:** follow the repository's PR rules; open the PR and push only with permission, as the next part says; once the PR is open, turn on Auto-fix with the ccd_pr tools; address every review finding, including ones in a review's body (nitpicks, outside-diff comments), batching fixes into one push. Do not merge: the implementation manager merges once every check passes, no thread is unresolved, and the review covers the head commit, so resolve a thread only after its fix is pushed or after replying with the reason you are not acting on it.
8. **Review and push permission**, written out in full. `<run>`, `<name>` and `<sha>` in the poll are placeholders: write the absolute run-folder path and the unit name into the prompt; `<sha>` is the full sha the child asks about, which it fills in.
   - Review: when the change is complete and its tests pass, run `/branch-review:review` with the unit's intent (the spec's goal, which becomes the PR description) and continue until it converges. Run it yourself, not from a subagent implementing a slice.
   - Escalations: for each escalated finding, message the implementation manager with the question, the finding id and your recommendation. When it forwards the owner's answer, record it with `/branch-review:review --resolve <id> "<the owner's quoted words>"`, then run the review to convergence again.
   - Ask to push, before opening the PR and before every later push: commit everything; sync with `<base>` (ccd_host `sync_with_base_branch`); run the review again if anything but a clean base update changed since it last converged; start this with Bash `run_in_background`: `f=<run>/pushes.log; until l=$(grep -E '^<name> (go|redo) <sha>( |$)' "$f" 2>/dev/null | tail -1) && [ -n "$l" ]; do sleep 30; done; echo "$l"`; then end your turn with `[push-request] <sha>`, the full sha of HEAD.
   - On `go <sha>`: push exactly that commit (open the PR the first time). If HEAD has moved since, do not push; ask again.
   - On `redo <sha> <reason>`: fix what the reason says (usually: run the review), commit, and ask again with the new sha. A reason that names no problem in your branch (`no-worktree-path`, `check-error`) is the manager's to fix: end your turn with `[waiting]`, and when it messages you, read the last line for that sha in `<run>/pushes.log`.
   - An Auto-fix wake-up (CI failure, review comment, merge conflict) is never permission to push. Fix it locally, commit, and ask.
   - Put the review summary's Escalated, Deferred and Invalid sections in the PR description under "Review notes".
9. **Status token:** "Begin every turn's recap with one of `[gated]`, `[building]`, `[reviewing]` (running `/branch-review:review`), `[fixing]`, `[push-request] <sha>` (asking to push that full sha), `[waiting]` (on a decision), `[blocked]`, or `[review]` (PR open and nothing left for you to do unless review or CI reports something)."
10. **Questions only:** "Do not send the implementation manager (`<your id>`) progress or merge reports. Message it only for a decision the owner must make, an escalated review finding included: the question, your recommended default, and what you will do meanwhile."

## Event handling

Every turn after setup, handle the turn's event; take the first row that matches. Unless the row says otherwise, the whole reply is one short line, with nothing about a child's progress.

| Event | Do |
|---|---|
| Work outside every unit turns up (in a note, message or PR) | Propose it as a card (`own_initiative`), once, its prompt built from the child prompt's parts without the gate. It merges like any unit and gates nothing. |
| The app reports the user started a card from this run | `get_session` it for its branch, fill in the table, start its watch. |
| Child note begins `[push-request] <sha>` | Invoke `branch-review:check --repo <worktree> --commit <sha>`, `<worktree>` being the child's working directory from `get_session`. Covered: `printf '%s go %s\n' '<unit>' '<sha>' >> <run>/pushes.log`. Not covered: `printf '%s redo %s %s\n' '<unit>' '<sha>' '<reasons>' >> <run>/pushes.log`, `<reasons>` as the check's last line lists them. `get_session` shows no working directory: append `redo <sha> no-worktree-path` and tell the user in one line. The check errors (exit 1): append `redo <sha> check-error` and tell the user its stderr line. Once either is sorted, run the check again, append its line and send the child one message to read it. Never send a `go` or `redo` by message. |
| Watch: `READY <pr> <sha>`, or a child's note begins `[review]` | Check the merge bar (below; for a `[review]` note, run the script with `ONCE=1` first, and on `NOT-READY` do nothing: the running watch covers it). Met: merge. Not met because the child is running or not at `[review]`: restart the watch with `SKIP_HEAD=<sha>`; the child's next `[review]` note brings you back. Not met because the check says not covered: send the child one message with the reasons, telling it to review and ask to push; restart the watch with `SKIP_HEAD=<sha>`. Not met because the check errors or `get_session` shows no working directory: tell the user in one line. |
| Watch: `MERGED <pr>` | If the table already records that merge (you merged it), ignore it. Otherwise someone else merged it: confirm with `gh`, then "After a merge". |
| Watch: `ATTENTION <pr> <sha> behind-base` | Send the child one message: sync with `<base>` and ask to push. Restart the watch with `SKIP_HEAD=<sha>`. |
| Watch: `CLOSED <pr>` | Tell the user; ask whether the unit is dropped or redone. Its dependents stay gated meanwhile. |
| Watch: `STALLED …` or `ERROR …` | Read the child's last few events (`list_events`, limit 5). Legitimately waiting (on a decision, a review still running, CI queued) or a passing hiccup: restart the watch. Otherwise tell the user what is stuck, in two lines. |
| Child note `[blocked]`, or a question to the owner | Relay it, as for a message. |
| Child message with an escalated review finding | Give the user the finding, the child's recommendation and yours, then wait. Forward the answer in one message, quoting the user's words; the child records it with `--resolve`. |
| Child message with a question | Give the user the question, the child's default and your recommendation, then wait. Forward the answer in one message, quoting the user's words, and add the decision to `notes.md` if later units need it. |
| Any other child note | Nothing. |
| The user changes the plan | Update the table in `notes.md`. To cancel a gated unit, append `<name> stop <reason>` to `gates.log`. A new unit needs a session: start it now (this is the user's turn) and watch it. |
| The user's message after an app restart or a long gap | Restart a watch for every open unit; background tasks may not have survived. |

**The merge bar,** all of:
- the watch printed `READY` for that head (or `ONCE=1` did);
- `get_session` on the child shows `isRunning: false` and its latest note begins `[review]`;
- `branch-review:check --repo <worktree> --commit <sha>` on the PR's head commit says covered.

For the `specs` unit, the bar is: the watch's `READY`, no unpushed fix in this session, and `branch-review:check` covered on the PR's head, with `--repo` this session's own working directory. Before each push of its branch, run `/branch-review:review` on it to convergence (escalated findings go straight to the user), then push only when `branch-review:check` says covered for that commit. The rows above that message or restart a child apply to this session's own work instead.

**Merge:** `gh pr merge <pr> -R <repo> --<method> --match-head-commit <sha>`, then confirm `MERGED` with `gh pr view` and record it in the table. If it fails because the head moved, restart the watch; for anything else (approval required, conflicts, branch protection), tell the user.

**After a merge:**
1. If the PR's title says `(part k of N)` with k < N, the unit isn't done: send the child one message that part k merged (it can't learn this otherwise), restart the watch on the child's current branch (`get_session`), and skip to step 4.
2. Read the child's latest note and the PR description. Append a section for the unit to `notes.md`: its PRs, and what later units must know that the specs don't say (owner decisions, changed names or ids, new mechanisms, limits found).
3. Open every gate whose prerequisites are now all merged: `printf '%s open\n' '<name>' >> <run>/gates.log`.
4. Reply with the tracker. Send no message to the child.

**A gate opened but its child never woke:** if a later turn finds a child still `[gated]` more than 15 minutes after its gate opened, its gate loop died; send it one message saying its gate is open.

## Finishing

When every unit is merged: confirm with `gh` that each PR merged into the base, and check whatever the repository uses to track the work (an epic table, a changelog). Report the merged PRs, decisions recorded along the way, and the follow-ups left (tech debt, deferred items, a release, tracking docs that are out of date). Propose real follow-ups as cards. Offer once to archive the child sessions; archive only on a yes.

## Tracker

After setup, after every merge, and whenever something waits on the user, end the reply with:

```
Run <run> (<owner/repo>)
- <unit> merged #<pr> · <unit> merged #<pr>
- <unit> PR #<pr> open, watching · <unit> building
- <unit> gated on <units>
- Waiting on you: nothing | <what>
```

## Red flags

| Thought | Reality |
|---|---|
| "I'll start the next unit when this one merges" | That turn is a background event; the start becomes a card. Start everything now, gated. |
| "The child will tell me when its PR is up" | Watch its branch from the start. Notes get missed. |
| "Merges notify me" | They don't, and passing checks don't either. Only the watch tells you. |
| "All checks pass, merge" | Also: no unresolved thread, child idle at `[review]`, and `branch-review:check` covered on *this* head. |
| "The child says its review is clean" | Run `branch-review:check` yourself. |
| "Auto-fix woke the child; it can push the fix" | It asks first, every time. |
| "It's only a merge from main" | The check decides; conflicts need a review. |
| "I'll message the go" | Append to `pushes.log`. |
| "The child is busy; restart the plain watch" | It re-fires on the same head every few minutes. Use `SKIP_HEAD`, after `ATTENTION` too. |
| "Part 1 merged, open the dependents' gates" | Not until part N. |
| "I'll let the child know its PR merged" | It doesn't need to know, except a non-final part. Every message spends the 10-send budget. |
| "The child gave a status update; I'll summarize it for the user" | One line at most, nothing about progress. |
| "The next prompt should mention what 5-3 changed" | Prompts are written up front. Put it in `notes.md` before opening the gate. |
