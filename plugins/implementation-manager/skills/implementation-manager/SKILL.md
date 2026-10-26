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

## Four constraints that shape everything

1. **Sessions can only be started on the user's own turn.** `start_session` with `"user_asked"` is refused on a turn started by a background task, a child's note or another session's message; it degrades to a card the user must click. So **start every child in the first turn**, and hold each one back with a *gate* (a file the child polls), not by starting it later.
2. **Messages are a scarce budget.** The app stops delivering a session's messages after 10 sends without the user typing in it. Coordinate through files in the run folder (gates, push requests and answers), never through status messages.
3. **Nothing notifies you of a push request, a ready PR or a merge** except the run watcher: `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py wait <run>` with `run_in_background`. It prints one event line and exits. Handle the event, then start it again before ending the turn. With open units and no `wait` running, the run is deaf.
4. **Child notes can be held for hours.** The app may hold a child's end-of-turn notes until the user types. Nothing waits on a note: children write push requests to `<run>/requests.log`, and you act on the watcher's lines and `run.py status`.

## The run script

`run.py <command> <args>` below is short for `python3 ${CLAUDE_SKILL_DIR}/../../scripts/run.py <command> <run> <args>`: the absolute run folder always comes right after the command. Write it out in full each time; shell variables do not survive between Bash calls. Only the script writes `run.json`, `gates.log` and `pushes.log`; never write them yourself. A refused command exits 1 with one line on stderr and changes nothing.

| Command | Does |
|---|---|
| `init --repo <owner/repo> --base <base> --method <merge\|squash\|rebase> [--wip N]` | Creates the run folder. The queue is `serial` (one PR in flight) unless the owner asks for `--queue parallel`. |
| `unit <name> [--prereq U ...] [--after U ... \| --after none] [--title T] [--branch B] [--worktree P] [--specs]` | Adds a unit (letters, digits, `-` and `_`), or updates the fields given. `--after` units must merge first, but don't gate the start; a stopped one counts as merged, and `--after none` clears the list. A unit can't both need and merge after the same unit, and cycles are refused. `--title` is shown beside the name in `status`. |
| `set wip <N\|none>`, `set queue <serial\|parallel>` | The owner's changes mid-run. In parallel mode it prints `queued <unit> <sha>` for each request still queued. |
| `open-ready` | Opens every gate that may open now (prerequisites merged, within the WIP limit) and prints their names. A unit that an open unit merges after is exempt from the WIP limit. |
| `open <unit>`, `stop <unit> <reason>` | Opens one gate; stops a unit for good (it never opens, gets `go` or queues again, and gives up the slot). |
| `go <unit> <sha>` | Allows the push. It refuses a held unit. In serial mode it also gives the unit the slot, and refuses while another unit holds it or is first in the queue, or while the unit merges after a unit not yet merged. |
| `queue <unit> <sha>` | Holds a covered request until the slot frees; the watcher then reports `NEXT`. A queued unit whose `--after` units haven't merged is skipped at the head, so the next one is served. It refuses a held unit. |
| `redo <unit> <sha> <reasons>` | Refuses the push with one line of reasons. A slot holder gives up the slot; an earlier `go` not yet pushed is spent. |
| `hold <unit> <reason>` | Holds the unit's merge until `release`, `merged`, `closed` or `stop`: no READY, REMIND or STALLED, and `go` and `queue` refuse it. It frees the slot if the unit holds it, drops its queue entry, and answers each waiting request (unanswered or queued) `redo <sha> held`. A done unit can't be held. |
| `release <unit>` | Lifts the hold and restarts the stall clock. |
| `yield <unit> <reason>` | The slot holder gives up the slot. Its PR stays open; it returns to the front of the queue when it asks again. A `go` it has not pushed stays outstanding: its old head is not reported ready. |
| `merged <unit> <pr> [--more]`, `closed <unit> <pr>` | Records a merge (`--more`: a part k of N with k < N) or a close, and frees the slot. |
| `status` | The tracker and every line under "needs the manager". |
| `clear-bad` | Forgets the bad request lines `status` lists, once you have handled them. |
| `check <unit>` | One readiness check of the unit's PR now: `READY <pr> <sha>`, `NOT-READY …`, `MERGED <pr>`, `CLOSED <pr>` or `ERROR …`. |
| `wait [--stall-hours N]` | Blocks until one event, prints it, exits 0. Always with `run_in_background`. |

## Setup (first turn)

1. **The work breakdown.** Use this conversation's plan if it has one; otherwise read the spec, plan or epic named in the arguments. List the units: name (short, letters, digits, `-` and `_`, e.g. `5-3`), title, spec path, prerequisites, merge-order units, and a `branch_name` you choose. A prerequisite (`--prereq`) is code the unit builds on: its gate waits for it. A merge-order unit (`--after`) is an order that only matters at merge, such as changes to the same docs or files: the unit starts at once and merges after it. Take order and dependencies from the docs; units the docs allow side by side run in parallel. If this session has its own open PR the units need (e.g. the specs), it is a unit named `specs` with no child, and every unit depends on it.
2. **Repository facts and review tools:** `owner/repo`, base branch, and merge method (the repository's instructions; otherwise `gh repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed`, and if more than one is allowed and nothing says which, ask). Confirm `branch-review:check` and `branch-review:review` are among your available skills. If either is missing, stop and tell the user to install branch-review (`/plugin install branch-review@mbcrawfo-skills`).
3. **The run folder:** `<run>` is `${CLAUDE_PLUGIN_DATA}/<owner>-<repo>/<slug>`, `<slug>` a slug of the work (e.g. `query-gen-phase-5`). It is an absolute path; write it out in full wherever `<run>` appears. If that path does not start with `/`, the plugin's data folder is unknown: stop and tell the user, and never pick a folder of your own. Run `run.py init` with the facts from step 2 and `--wip N` if the owner gave a WIP limit. Then `run.py unit <name> --prereq <units> --after <units> --title '<title>'` for each unit in table order, leaving out an option with nothing to give (`run.py unit specs --specs` first if there is a `specs` unit). Write `<run>/notes.md` with the units table: name, title, spec, prerequisites, merge-order units, session, branch, PRs.
   **Shared changes.** Before two gates open whose units the docs allow side by side, read both specs and add a "Shared changes" section to `notes.md`: the files or modules both will change, which unit owns each change, and its agreed shape where the specs say it. Part 6 of each sibling's prompt points at it.
4. **Go or ask.** If the breakdown and dependencies are stated in the docs or were agreed in this conversation, continue. If you inferred any dependency, or anything in steps 1–2 is ambiguous, show the units table and ask; start everything on the user's answer.
5. **Start every child now** (see "Starting children"). Every child's prompt carries its gate. If any start comes back as a card instead, say so and list it under "Waiting on you" in the tracker.
6. **Record and watch.** `get_session` on each child for its actual branch (the app adds a prefix and suffix) and working directory, and run `run.py unit <name> --branch <branch> --worktree <path>`; if either is not set yet, do it next turn. For `specs`, record this session's own branch and working directory. Then start one `run.py wait` with `run_in_background`.
7. Run `run.py open-ready` and reply with the tracker.

## Starting children

If this session already did other work, call `mcp__ccd_session__mark_chapter` first. Write a bold title line and one sentence for each session right before its call. Each call: `initiation: "user_asked"`, `context: "fresh"`, `use_worktree: true`, `name`, `title` and `branch_name` from the unit. `get_session` with `"self"` gives your own id for the prompt.

The child prompt has these parts, in this order:

1. **Goal:** implement `<unit title>` as specified in `<spec path>`, delivered as one PR to `<base>` on `<owner/repo>`. If the repository's PR size rule forces several, they land in order, one at a time, each titled with `(part k of N)` (part 11 says how).
2. **Gate:** "Do not start yet. Run this with Bash `run_in_background`, then end your turn with `[gated]`; it prints when you may start: `f="<run>/gates.log"; until l=$(grep -E '^<name> (open|stop)' "$f" 2>/dev/null | tail -1) && [ -n "$l" ]; do sleep 60; done; echo "$l"`. On `open`, continue with the next step. On `stop`, do nothing and end your turn quoting the line." Use the absolute run-folder path and the unit name.
3. **Start:** bring the branch up to date with `<base>` (ccd_host `sync_with_base_branch`); confirm the prerequisites' code is in the tree, and stop and say so if not; read `<run>/notes.md`, which records what earlier units actually merged.
4. **Read:** the repository's agent instructions (root and every folder touched), the spec, and the parts of the epic or parent docs it names. Repository rules win over any skill's defaults. Where merged code differs from the spec's assumptions, follow the code and say so in the plan.
5. **Plan and build:** write the plan with superpowers:writing-plans where the repository keeps plans, then implement it, preferably with superpowers:subagent-driven-development, validating every commit and push the way the repository requires.
6. **Unit-specific instructions:** the owner's decisions and constraints for this unit; for a parallel sibling, which other unit is running, which shared files you both touch, and to keep those changes small.
7. **Pull request:** follow the repository's PR rules; open the PR and push only with permission, as the next part says; once the PR is open, turn on Auto-fix with the ccd_pr tools; address every review finding, including ones in a review's body (nitpicks, outside-diff comments), batching fixes into one push. Do not merge: the implementation manager merges once every check passes, no thread is unresolved, and the review covers the head commit, so resolve a thread only after its fix is pushed or after replying with the reason you are not acting on it.

Parts 8 to 11 follow, word for word. Replace `<run>` (the absolute run folder), `<name>` (the unit), `<base>` and `<manager id>` (your session id); leave `<sha>`, `<branch>`, `<reasons>`, `<id>` and `k` for the child.

```
8. Review and push permission.
- Review: when the change is complete and its tests pass, run `/branch-review:review --base origin/<base>` with the unit's intent (the spec's goal, which becomes the PR description) and continue until it converges. Later runs reuse the stored base. Run it yourself, not from a subagent implementing a slice. Run it again when a later change is complete or a `redo` from the manager says so; never decide on your own that a sync from the base needs one.
- Escalations: send all the escalated findings of a converged review (outside-the-change ones included) to the implementation manager in one message (each message spends the 10-send budget), each with the question, the finding id and your recommendation. When it forwards the owner's answer, record it with `/branch-review:review --resolve <id> "<the owner's quoted words>"`, then run the review to convergence again.
- Ask to push, before opening the PR and before every later push: commit everything. Then start this command with Bash `run_in_background`; it records your request and prints the manager's answer. `<sha>` is the full sha of HEAD and `<branch>` the output of `git branch --show-current`: `f="<run>/pushes.log"; n=$(cat "$f" 2>/dev/null | wc -l); printf '%s push %s %s\n' '<name>' '<sha>' '<branch>' >> "<run>/requests.log"; until l=$(tail -n +"$((n + 1))" "$f" 2>/dev/null | grep -E '^<name> (go|redo) <sha>( |$)' | tail -1) && [ -n "$l" ]; do sleep 30; done; echo "$l"`. Then end your turn with `[push-request] <sha>`. The answer counts only lines added after the command starts, so asking again with the same sha is fine.
- On `go <sha>`:
  1. If HEAD is no longer that sha, do not push; ask again.
  2. Sync with `<base>` (ccd_host `sync_with_base_branch`).
  3. A clean merge, or nothing to merge: run the validation the repository requires before a push; if it passes, push HEAD (open the PR if none is open for the branch). HEAD may be the clean merge commit rather than `<sha>`; that is expected and needs no new request.
  4. The merge conflicts: resolve it, commit, and ask again. The manager's answer says whether a review is needed (`base-conflict`); the review then checks the resolutions in a small merge-check round.
  5. Validation fails: fix it, commit, and ask again.
- On `redo <sha> <reasons>`: fix what the reasons say, commit if anything changed, and ask again, with the same sha if nothing did.
  - `changes-since-review`, `open-findings`, `unverified-fixes`, `base-conflict`, `no-rounds`, `partial-scope`, `history-rewritten`, `snapshot-missing`, `second-opinion-pending`: run the review to convergence, with no new arguments; it picks the round itself (a merge-check round for `base-conflict`, the second opinion for `second-opinion-pending`). `changes-since-review` right after a review that found nothing to review means the working tree holds files the commit lacks: commit or remove them.
  - `escalated`: the findings wait for the owner's answer. With no other review reason, end your turn with `[waiting]` and do not review again. With another one, run the review for it, then wait.
  - `wrong-branch`: the request's branch is not the one checked out in this session's own worktree, or that is not this session's branch. Get back to this session's own worktree and branch, then ask again.
  - `pr-merged`: your unit's last PR has already merged. Push nothing; end your turn with `[waiting]`, saying in the recap what the change is and whether it still matters.
  - `unit-stopped`: the owner stopped this unit. Push nothing; end your turn with `[waiting]`.
  - `no-worktree-path`, `check-error`: the manager's problem, not your branch's. End your turn with `[waiting]` and ask again when it tells you to.
- After the manager forwards an owner answer while your PR is open, always ask again once you have applied it, with the same sha if nothing changed. This is how your PR gets back in line.
- If the manager tells you to read `<run>/pushes.log`, act on its last line for your sha.
- If the manager tells you this unit is stopped, push and commit nothing more, and end your turn with `[waiting]` quoting its message.
- An Auto-fix wake-up (CI failure, review comment, merge conflict) is never permission to push. Fix it locally, commit, and ask.
- Put the review summary's Escalated, Deferred, Invalid, Outside the change and Intent check sections in the PR description under "Review notes".

9. Status token. Every turn's final message begins with one of `[gated]`, `[building]`, `[reviewing]` (running `/branch-review:review`), `[fixing]`, `[push-request] <sha>` (asking to push that full sha), `[waiting]` (on a decision or the manager), `[blocked]`, or `[review]` (PR open and nothing left for you to do unless review or CI reports something). This includes turns woken by a subagent's hand-back or a background task. A turn that applies a message from the manager says which after the token, for example `[reviewing] owner answer applied`. A wake-up for bot-only PR comments (coverage reports, "Review skipped" notices, anything with nothing to fix) ends the turn at once with your previous token unchanged, and sends no message.

10. Questions only. Do not send the implementation manager (<manager id>) progress or merge reports. Message it only for a decision the owner must make, an escalated review finding included: the question, your recommended default, and what you will do meanwhile.

11. One open PR at a time, and scope. Build every part in this session's own worktree, on its own branch. While part k's PR is open, you may plan part k+1 but commit nothing for it; start its code only after the manager tells you part k merged, then sync with `<base>` first. Never use a second branch, worktree or checkout. A finding whose fix belongs outside this unit's scope is escalated with the review's other escalations. A finding outside the change (status `outside`) stays as the review leaves it and goes in the PR description; the review itself escalates it when the repository's rules forbid deferring review findings. Never record any review finding as new tech debt or a follow-up without the owner's answer.
```

## Event handling

Every turn: handle what woke you, then make sure exactly one `run.py wait` is running before you end the turn (start one when the last one has printed its line, or when you cannot tell whether it survived an app restart). Take the first row that matches. Unless the row says otherwise, the whole reply is one short line, with nothing about a child's progress.

**On every user message,** before anything else: run `run.py status` and act on each line under "needs the manager". Where a unit has a title, `status` shows it in brackets after the name; commands take the bare name.
- `request <unit> <sha> <branch> unanswered since …`: handle it as `REQUEST`.
- `hold <unit>: <reason> since …`: nothing to do; list it under "Waiting on you" in the tracker.
- `<unit> PR #<pr> ready since …`: run `run.py check <unit>`; on `READY <pr> <sha>`, handle it as `READY`.
- `push approved for <unit> <sha7>, not seen yet`: `list_events` on the child (limit 5). Idle, with its latest note `[push-request]` for that sha: its answer poll died. If the unit holds the slot (or the queue is parallel), send it one message to read `<run>/pushes.log`; if it gave the slot up, send it one message to ask again. Otherwise nothing; it is syncing, validating or fixing, or waiting on the owner.
- `slot holder <unit> PR #<pr> behind base`: handle it as `BEHIND`, unless `list_events` on the child (limit 5) shows it already syncing.
- `slot free with a queue, next <unit> <sha>`: handle it as `NEXT`.
- `requests.log has lines no wait has read yet`: start a `wait`; it reports those you have not answered yet.
- `bad request line: …`: if a child wrote it, send that child one message with the exact request line format. Tell the user in one line otherwise. Once every listed line is handled, run `run.py clear-bad`.
- `gate <unit> may open`: run `run.py open-ready`.

| Event | Do |
|---|---|
| `REQUEST <unit> <sha> <branch>` or `PENDING <unit> <sha> <branch>` | "Answering a push request", below. Send the child no message. `PENDING` is a request still unanswered 15 minutes after its `REQUEST`: handle it exactly like `REQUEST`. |
| `NEXT <unit> <sha>` | The slot is free and this queued request is first. Answer it again (the child may have changed something): covered gets `go`, not covered gets `redo`. Do not tell the child to sync first; the `go` covers it. |
| `READY <unit> <pr> <sha>` or `REMIND <unit> <pr> <sha>` | Apply the merge bar (below) and merge. The child may be running; that does not matter. |
| `MERGED <unit> <pr>` | If you already ran `run.py merged` for that PR, ignore it. Otherwise someone else merged it: confirm with `gh pr view`, then "After a merge". |
| `CLOSED <unit> <pr>` | `run.py closed <unit> <pr>`. Tell the user; ask whether the unit is dropped or redone. Its dependents stay gated meanwhile. |
| `BEHIND <unit> <pr> <sha>` | Send the child one message: sync with `<base>` and ask to push. |
| `STUCK <unit> <pr> no-push` or `STUCK <unit> - no-push` | `list_events` on the child (limit 5). Idle at `[push-request] <sha>`, with `<unit> go <sha>` in `pushes.log` after that request: its answer poll died. Do not yield; send it one message to read `<run>/pushes.log`. At `[pushing] <sha>`, or syncing, validating or pushing: nothing; it is acting on the `go`, and its push, its next request or a `STALLED` settles it. Only at `[waiting]` or `[blocked]`, or with its session gone or idle at anything else: `run.py yield <unit> stuck`; a cause the owner must decide: tell the user in one line. |
| `STUCK <unit> <pr> <reasons>` (failing checks, unresolved threads, a conflict) | Serial mode: `run.py yield <unit> stuck`. `list_events` on the child (limit 5); a cause the owner must decide: tell the user in one line. Otherwise nothing; it asks again when it is ready. |
| `ORPHAN <unit> <pr>` | A PR opened on a done unit's branch after its last merge. Tell the user in one line what it changes (`gh pr view <pr> -R <repo>`) and ask whether to merge or close it. Merge: the merge bar, with `run.py check <unit>` for readiness, then `run.py merged <unit> <pr>`. Close: `gh pr close <pr> -R <repo>`, then `run.py closed <unit> <pr>`. |
| `STALLED <unit> <pr> head=<sha7><reasons>` | `list_events` on the child (limit 5). Legitimately waiting (on a decision, a review still running, CI queued): nothing. Otherwise tell the user what is stuck, in two lines. |
| `ERROR <message>` | Tell the user the line. `gh` failures: start the `wait` again. A run folder it cannot read: run `run.py status` for the cause, and fix it or wait for the user before starting the `wait` again. |
| Child message with an escalated review finding or a question, or a `[blocked]` note | Give the user the finding or question, the child's recommendation and yours, in the reply text, and list it under "Waiting on you". An escalated finding, or a question that says "blocks merge: yes": `run.py hold <unit> <short reason>`. A question that says "blocks merge: no": relay it and hold nothing; a `READY` that meets the merge bar is merged. A `[blocked]` note while the unit holds the slot: `run.py yield <unit> blocked`. |
| The user answers a question you relayed | If the unit's last PR has already merged, forward nothing: an answer that needs no work on that branch is only recorded, and one that needs work becomes a card (the row on work outside every unit). Otherwise forward the answer to the child in one message, quoting the user's words (an escalated finding: the child records it with `--resolve`; it then asks to push again), then `run.py release <unit>` if it is held. Add the decision to `notes.md` if later units need it. |
| A child message that says nothing waits on the answer | Apply what it asks (a `notes.md` entry, an `--after`) and send no reply. |
| Any other child note | Nothing. The reply stays one line, for a note from a child at `[designing]` too. |
| Work outside every unit turns up (in a note, message or PR) | Propose it as a card (`own_initiative`), once, its prompt built from the child prompt's parts, gate included. It merges like any unit and gates nothing. |
| The app reports the user started a card from this run | `get_session` it; `run.py unit <name> --branch <branch> --worktree <path>`, then `run.py open-ready`; fill in the table. |
| The user adds a unit | This is the user's turn, so start it now. Run `run.py unit <name> --prereq <units> --after <units> --title '<title>'` (only the options with something to give), then `run.py open-ready`. Start its session ("Starting children"); its prompt drops the gate part only if `open-ready` just opened it. If the user will design it with the child, the prompt adds the `[designing]` token: until its first push request, its questions go to the user in its own session; after that, a question that blocks the merge also comes to you as a one-line notice ("blocks merge: yes", and where the question is), which you handle like any blocking question. Its part 10 covers only ordering conflicts with other units. Record its branch and worktree, and add it to the table in `notes.md`. |
| The user adds a prerequisite to a unit | Not started yet (listed under `gated`): before `run.py unit <unit> --prereq <units>`, tell the user in one line the wait it costs (until those units merge) and offer `--after` instead. When the conflict is merge order alone (the same docs or files, no code dependency), apply `--after` without asking. |
| The user changes the plan | WIP limit: `run.py set wip <N\|none>`; lowering it never stops a unit already open. Queue mode: `run.py set queue parallel` only when the owner asks; for each `queued <unit> <sha>` it prints, answer the request again ("Answering a push request": a fresh check, covered gets `go`). Cancel a unit: run `run.py status` first. Listed under `gated`: `run.py stop <unit> <reason>` alone. Listed under `open` (`<unit> PR #n` or `<unit> working`; it has started and never reads `gates.log` again): `run.py stop <unit> <reason>`, then send its child one message that the unit is stopped, quoting the reason, and ask the owner whether to close its open PR. Update the table in `notes.md`. |

**Answering a push request** (`REQUEST` or `PENDING <unit> <sha> <branch>`, or `NEXT <unit> <sha>` with the unit's branch from `run.json` as `<branch>`). Work through these steps in order; the first `redo`, `go` or `queue` you run ends it:
1. The unit is done (you ran `run.py merged` for its last PR; `run.py go` refuses it too): `run.py redo <unit> <sha> pr-merged`, then ask the owner whether the change becomes a follow-up (`list_events` on the child shows what it is). The unit is stopped (`status` lists it `stopped`; `go` and `queue` refuse it): `run.py redo <unit> <sha> unit-stopped`.
2. Read the unit's branch and worktree from `<run>/run.json` (under `units`). Either missing: `get_session` on the child and record them with `run.py unit <unit> --branch <branch> --worktree <path>`. Still no working directory: `run.py redo <unit> <sha> no-worktree-path` and tell the user in one line.
3. Invoke `branch-review:check --repo <worktree> --commit <sha>`. It errors (exit 1): `run.py redo <unit> <sha> check-error` and tell the user its stderr line.
4. The check JSON's `branch` differs from the request's branch or from the unit's branch in `run.json`: `run.py redo <unit> <sha> wrong-branch`.
5. The unit is held (`status` lists `hold <unit>`; `go` refuses with `is held`): `run.py redo <unit> <sha> held`.
6. Covered: `run.py go <unit> <sha>`. If it refuses because another unit holds the slot or is first in the queue, or (serial mode) because the unit `merges after` a unit not yet merged: `run.py queue <unit> <sha>`.
7. Not covered: `run.py redo <unit> <sha> '<reasons>'`, `<reasons>` the JSON's `reasons` joined by `, ` (no `not covered: ` prefix).

Once a `no-worktree-path` or `check-error` is sorted out, send the child one message to ask again.

**Questions to the owner** go in the reply text, with the child's recommendation and yours, and under "Waiting on you" in the tracker. Never use the pop-up question tool: while it waits, no event is handled. The answer arrives as a normal user message.

**Messages** go to a child only for what no file carries: an owner answer, a `BEHIND` sync, a merged part k of N, a dead poll, a request format fix, a merge bar the check failed, a stopped unit that had started. Never send one alongside a `go`, `redo` or `open`, or a `stop` to a unit still gated: the child's poll reads the file within a minute. A started unit's `stop` is the one exception, since nothing polls `gates.log` for it any more. Never mention reviews; `redo` reasons say when one is needed. A gated child gets a message only if it is still `[gated]` 5 minutes or more after its gate opened: its gate loop died, so tell it its gate is open.

## The merge bar

All of:
- `READY` or `REMIND` for that PR and head, or `run.py check <unit>` printing `READY <pr> <sha>`;
- `branch-review:check --repo <worktree> --commit <sha>` covered on that head, `<worktree>` the unit's worktree;
- no unanswered or queued request from that unit, and in serial mode the slot is free or held by that unit (`run.py status` shows both);
- the unit is not stopped (`run.py status` does not list it `stopped`);
- the unit is not held and every `--after` unit has merged (`run.py status` shows neither `hold <unit>` nor `<unit> … merges after`);
- no approved push is outstanding for that unit (`run.py status` does not list `push approved for <unit>`). The child is about to push a newer commit, so wait for it; a `READY` for its new head follows.

The child being idle is not required: it cannot push without a `go`, an outstanding `go` holds the merge, and `--match-head-commit` guards it.

Not met because the check says not covered: send the child one message: its PR head `<sha7>` is not covered (`<reasons>`); fix that and ask to push. Not met because the check errors or the unit has no worktree: tell the user in one line. An unanswered request: answer it first. A queued request, another unit's slot, a hold or an unmerged `--after` unit: do nothing; the unit's turn comes through `NEXT`, its next request or the watcher's next `READY`.

**Merge:** `gh pr merge <pr> -R <repo> --<method> --match-head-commit <sha> && gh pr view <pr> -R <repo> --json state --jq .state`, which prints `MERGED`. If it fails because the head moved, do nothing more: the watcher reports the new head. For anything else (approval required, conflicts, branch protection), tell the user.

**After a merge:**
1. If the PR's title says `(part k of N)` with k < N: `run.py merged <unit> <pr> --more`, send the child one message that part k merged and it may start part k+1, and skip to step 4.
2. `run.py merged <unit> <pr>`.
3. Save the PR description with `mkdir -p <run>/prs && gh pr view <pr> -R <repo> --json body --jq .body > <run>/prs/<unit>-<pr>.md`, and read from that file only what `notes.md` needs (and the child's latest note, if one arrived). Read the app's shared notes folder for files the merged unit wrote (`<unit>-notes.md`). The app adds its path to each child's prompt: look for it among your own additional working directories, or else in one child's first prompt (`list_events`). Append a section for the unit to `notes.md`: its PRs, and what later units must know that the specs don't say (owner decisions, changed names or ids, new mechanisms, limits found), from either source. `notes.md` stays the one record you keep. Then `run.py open-ready`.
4. Reply with the tracker. Send no other message to the child.

## The `specs` unit

This session's own PR goes through the same queue. Before each push of its branch, run `/branch-review:review --base origin/<base>` on it to convergence (escalated findings go straight to the user; later runs reuse the stored base). Then handle it as a `REQUEST` for `specs` with this session's own working directory: covered gets `run.py go specs <sha>` or `run.py queue specs <sha>`; once it has the `go` (at once, or after `NEXT specs`), sync with `<base>`, validate, and push, as a child does on `go`. Its merge bar is the same. The rows that message a child apply to this session's own work instead.

## Finishing

When every unit is merged: confirm with `gh` that each PR merged into the base, and check whatever the repository uses to track the work (an epic table, a changelog). Report the merged PRs, decisions recorded along the way, and the follow-ups left (tech debt, deferred items, a release, tracking docs that are out of date). Propose real follow-ups as cards. Offer once to archive the child sessions; archive only on a yes.

## Tracker

After setup, after every merge, and whenever something waits on the user, end the reply with this, built from `run.py status`. Name units as `status` lists them, with the title in brackets when there is one (e.g. `column-overrides (8.7)`).

```
Run <run> (<owner/repo>)
- Slot: <unit> since <HH:MM> | free
- Queue: <unit> · <unit> | empty
- <unit> merged #<pr> · <unit> merged #<pr>
- <unit> PR #<pr> open · <unit> building · <unit> PR #<pr> open, merges after <unit>
- <unit> gated on <units>
- Waiting on you: nothing | <what> (each hold among them)
```

## Red flags

| Thought | Reality |
|---|---|
| "I'll start the next unit when this one merges" | That turn is a background event; the start becomes a card. Start everything now, gated. |
| "I'll wait for the child's note" | Notes can be held for hours. Act on the watcher's lines and `run.py status`. |
| "Merges notify me" | They don't, and passing checks don't either. Only `run.py wait` tells you. |
| "The wait printed its line; I'll restart it later" | Restart it before ending the turn. Without it the run hears nothing. |
| "I'll ask the owner with the question tool" | It blocks every event behind it. Ask in the reply text. |
| "All checks pass, merge" | Also: `branch-review:check` covered on *this* head, and no unanswered request. |
| "The PR is ready but the child is running" | Merge if the bar is met. The child cannot push without a `go`. |
| "The child says its review is clean" | Run `branch-review:check` yourself. |
| "It's only a clean sync, it needs a review" or "it's only a merge from the base, no check needed" | The check decides both: a clean base merge is covered, a conflicting one says `base-conflict`. |
| "The Review notes list findings outside the change, and this repo forbids deferring; refuse the push" | `outside` is not a deferral (`deferred` means the owner chose it), and the check decides the push and the merge. Where the repo forbids deferring, the review escalates outside findings and the check says `escalated`; relay them like any escalation. If such a repo's Review notes still list outside findings the check does not count as escalated, give them to the owner as a question, and do not hold the push. |
| "The next queued unit should sync first" | The `go` covers it: the child syncs right before it pushes. |
| "I'll append the go to `pushes.log` myself" or "I'll message the go" | `run.py go`; it keeps the slot and the queue. Never a message. |
| "`STUCK … no-push`: yield, then tell the child to read `pushes.log`" | A dead poll is the likely cause. After a yield, that old `go` would push without the slot. Message only, no yield. |
| "`STUCK … no-push` and the child is syncing; yield" | It is acting on the `go`. Yield only when it is at `[waiting]` or `[blocked]`, or gone. |
| "The holder is waiting on the owner; it keeps the slot" | A blocking question or an escalation: `run.py hold`, which frees the slot. It asks again once you forward the answer. |
| "I'll hold this PR until the owner answers" | `run.py hold`, never memory. Only `hold` stops the watcher reporting it `READY`. |
| "8.6 conflicts with 8.7, add a prereq" | `--after` if it's only merge order. A prerequisite keeps the unit gated until the other merges. |
| "The child says nothing waits on me; I'll confirm" | No reply. Apply what it asks and spend no send. |
| "Auto-fix woke the child; it can push the fix" | It asks first, every time. |
| "Part 1 merged, open the dependents' gates" | Not until part N: `merged --more` keeps the unit open. |
| "I'll let the child know its PR merged" | It doesn't need to know, except a non-final part. Every message spends the 10-send budget. |
| "The child gave a status update; I'll summarize it for the user" | One line at most, nothing about progress. |
| "The next prompt should mention what 5-3 changed" | Prompts are written up front. Put it in `notes.md` before opening the gate. |
