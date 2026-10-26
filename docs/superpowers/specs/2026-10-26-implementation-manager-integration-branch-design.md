# Implementation manager: integration-branch mode

Date: 2026-10-26. Status: awaiting owner approval.

## Intent

Today the implementation manager merges each unit's PR into the base branch (usually `main`) as soon as it is ready. For a complex change, that means `main` holds a half-built feature for as long as the run lasts.

This change adds an opt-in **integration-branch mode**. The run's units merge into a dedicated integration branch instead of `main`, and one final PR lands the whole change in `main` at once. `main` never sees an intermediate state. Default behavior is unchanged: without the mode, a run works exactly as it does now.

The request was first framed as stacked PRs with the gh-stack extension. The goal turned out to be only keeping `main` clean, not per-layer review of a stack. An integration branch does that while keeping everything the manager already does well: parallel units, the push queue and slot, holds, and a per-unit merge bar. Stacks would have forced the units into a straight line, rebased every layer above any fix (voiding their reviews), and needed repo support for stacked PRs and no merge queue.

## Goals

- An owner can ask for integration-branch mode when starting a run; `main` then receives the run's work only through one final PR.
- Units run, review, queue and merge exactly as today, with the integration branch as their base.
- Changes landing on `main` during the run are brought into the integration branch as they land, so conflicts surface early.
- Conflicts between `main` and the integration branch are resolved by a session that owns the integration branch, and the resolutions are reviewed with a focused review, not a whole-feature one.
- The owner decides, explicitly, who merges the final PR and whether a whole-feature review runs before it.

## Non-goals

- Stacked PRs or any use of gh-stack.
- Changing the default (non-integration) run in any way.
- A user-facing branch-review feature for skipping review. The new branch-review step exists only for this protocol.

## Anti-goals

- No default for who merges the final PR. The owner must choose; the manager never assumes.
- `main` must never receive part of the run's work before the final PR.
- The new branch-review step must never let unpushed local work count as reviewed.
- The manager still writes no product code; conflict resolution belongs to a child.

## Constraints (from the current design)

- Sessions can only be started on the owner's own turn, so every child, the integration child included, starts in the first turn.
- Child messages are a scarce budget (10 sends without the owner typing); coordinate through files in the run folder.
- Only the run script writes `run.json`, `gates.log` and `pushes.log`.
- Plugin scripts are Python 3.9+, standard library only. Tests in `plugins/<name>/tests/`. Bump each plugin's version. Run `claude plugin validate`.
- The manager only treats a PR as ready when checks have run and passed (`no-checks` is not ready).
- branch-review's coverage rule: a commit is covered when its tree equals the last recorded round's snapshot, allowing a clean merge of the base since then. A base merge with conflicts makes the next round a **merge-check**, which reviews only the resolutions, the files both sides changed, and the branch's own work since the last round.

## Approaches considered

1. **gh-stack stacked PRs.** Rejected: linear stacks only, rebase cascades void reviews in other children's worktrees, needs stacked PRs enabled and no merge queue, and `gh stack merge` has no `--match-head-commit` guard. Per-layer review, its main benefit, is not wanted here.
2. **Integration mode in the run script (chosen).** The mode, the integration branch and the owner's two choices live in `run.json`; the integration child is a unit kind; the final gate and the final-merge rule are enforced by the script. More code and tests, but the rules live where tests cover them, not in prose the manager must remember after hours of event-driven turns. This repo's history shows most review findings on this plugin were in prose rules.
3. **Mostly skill text.** The integration child as an ordinary ungated unit, the choices in `notes.md`, the skill telling the manager when to apply them. Smaller, but rejected for the reason above.

For the branch-review side, one approach: a new "recorded" round type (below). The alternative, teaching `check` to trust a list of merged PRs, was dropped: it would put PR knowledge into a plugin that otherwise only knows git, and still need a new state format.

## Design

### 1. branch-review: the `record` step

A new `review_state.py record` subcommand and a thin `branch-review:record` skill.

**What it does.** Saves a round of mode `recorded` at a commit, with a reason, and no reviewer. The round becomes the anchor for later `check`, follow-up and merge-check calculations exactly like a reviewed round.

**Command.** `rs record --commit <sha> --reason-file <path> [--base <ref>]`. `--commit` defaults to `HEAD`. The reason is required and must be non-empty (for example "merged #12, #14 into integration/x; each reviewed on its own branch").

**Guardrail (hard rule in the script).** The commit must be equal to or an ancestor of the remote-tracking ref `refs/remotes/origin/<current branch>`. Anything else is refused with one stderr line. It records what has already landed on the remote branch, never local work. The skill tells the caller to `git fetch origin <branch>` first. (Builder's call whether to also confirm against the live remote with `git ls-remote`.)

**The round it saves.** Same shape as other rounds: `number`, `mode: "recorded"`, `snapshot` and `head` both the commit, `base_ref`, `merge_base`, `diff_from` (the previous anchor's snapshot, or the merge base when there is no earlier round), `diff_to`, `changed_files`, `reason`, `plugin_version`, and `added: []` so it counts as recorded. Model is null.

- Saved even when the change set is empty, if no recorded round exists yet. A fresh integration branch equals `main`, and `check` needs an anchor.
- A no-op (reported, nothing saved) when the commit is already the anchor's head.
- Existing findings are untouched. Open, unverified or escalated findings still block coverage.

**How other logic treats it.**

- `covered` and `choose_mode` need no special case: the round is an anchor. After a record, a clean base merge is covered. A conflicted one is a merge-check from the recorded snapshot, so it sees only the resolutions and the overlapping files.
- Recorded rounds don't count toward the six-round cap, don't trigger or satisfy a second opinion, and are never "the latest deep round".
- `_partial_scope` walks through them; they have no `paths`.
- The summary and `review-notes.md` list them as recorded, with their reason, for example `Rounds: 1 recorded, 2 merge-check (opus)`. They never read as a review.

**The skill.** `plugins/branch-review/skills/record/SKILL.md`, frontmatter:

- `user-invocable: false`, so it is hidden from the `/` menu. Claude can still invoke it; `disable-model-invocation` would lock out the integration child too.
- `description`: "Only when the implementation-manager integration-branch protocol tells you to record commits already merged on the remote branch. Never in response to a user's request, and never to get a branch past review."

The body works like `check`'s: the full `rs` command with the `CLAUDE_PLUGIN_DATA` prefix, fetch first, print the result. The README documents it under a short "For other plugins" heading, not among the user features.

Bump branch-review to 0.10.0.

### 2. implementation-manager: the run script

**`init` options.** `init ... --integration <branch> --final-merge <manager|owner> --feature-review <yes|no>`.

- `--integration` turns the mode on. `--final-merge` and `--feature-review` are then required, and refused without it.
- `run.json` gains `integration: {branch, final_merge, feature_review}` (null when the mode is off). `load` defaults it to null for older runs.
- `base` stays `main` (the final PR's target). Commands and prompts that mean "the units' base" use the integration branch in this mode. The builder decides whether that is a derived helper or a stored field.

**`set` options.** `set final-merge <manager|owner>` and `set feature-review <yes|no>`, only in integration mode. They're refused once the final gate has opened.

**The integration unit.** `unit <name> --integration` marks a unit as the integration unit (like `--specs`):

- At most one per run, and only in integration mode.
- No `--prereq` or `--after` of its own, and no other unit may name it in either.
- `open-ready` opens its gate at once, outside the WIP limit.
- Its branch is the integration branch.

**The final gate.** Once every other unit is done or stopped, `open-ready` appends `<integration unit> final` to `gates.log` and prints it. At least one unit must be done; if every unit stopped, it prints nothing and `status` says so, so the manager can ask the owner. The gate is written once.

**Late work.** In integration mode every piece of work the run adds targets the integration branch, before or after the final gate. That covers a unit the owner adds, a card for work outside every unit, an ORPHAN PR on a done unit's branch, and an owner-approved fix from an escalation.

- `unit` is still allowed after the final gate. The gate stays open; the integration child has already started its final steps.
- The final PR waits for that work to land. The watcher and `check` never report the final PR ready (`READY` or `FINAL-READY`) while any other unit is neither done nor stopped. `status` lists `final PR waits on <units>`, and the merge bar gains the same condition.
- `set final-merge` and `set feature-review` stay refused after the final gate.

**Slot release for direct pushes.** The integration unit's sync pushes go straight to the integration branch, with no PR, so nothing would free its slot.

- The watcher polls the integration branch's head (`gh api repos/<repo>/branches/<branch>`) while that unit holds the slot with a `go` outstanding.
- When the head equals the `go` sha, it reports `SYNCED <unit> <sha>`.
- The manager runs `run.py synced <unit> <sha>`, which frees the slot and clears the `go`.
- Until then the existing `STUCK … no-push` timing applies, as for any holder. Without a PR, the `summary is None` path must not report `no-push` once the head has landed.

**A sync in flight blocks unit merges, in both queue modes.**

- While the integration unit has an unanswered, queued or approved-but-unpushed request, no unit PR may merge into the integration branch. `status` lists `integration sync in flight`, and the merge bar gains that condition.
- In serial mode the slot already gives this. In parallel mode it is what keeps the child's reset-and-redo step from looping forever under steady merges.
- `SYNCED` and `synced` work whether or not the unit holds the slot: they clear the outstanding `go`, and free the slot only if it was held.

(My call. The alternative, refusing integration mode with `--queue parallel`, would take an option away from the owner.)

**Queue priority.** A queued request from the integration unit is served before every other queued request: every unit PR builds on the integration branch, so `main`'s changes should get in first. (My call.)

**The final PR.**

- The integration unit's PR (integration branch into `main`) is tracked by the watcher like any unit PR.
- With `final_merge: manager`, the merge bar applies as today.
- With `final_merge: owner`:
  - The watcher and `run.py check` never print `READY` or `REMIND` for it. They print `FINAL-READY <unit> <pr> <sha>` instead, once per head. `status` lists `final PR #<pr> ready: the owner merges it`.
  - The manager merges with `gh pr merge` itself (the run script has no merge command, and adding one would change the default run). So the guard is that no event the skill merges on is ever produced for this PR, and the merge bar gains "not the final PR of an owner-merges run". (My call.)
  - The watcher's `MERGED` event, when the owner merges, flows into "After a merge" as usual.

**Status and tracker.** `status` shows the mode, the integration branch and both choices. The tracker gains a line `Integration: <branch> into <base> · final merge: manager|owner · feature review: yes|no`.

Bump implementation-manager to 0.5.0. Setup confirms `branch-review:record` is available, as it does for `check` and `review`. If the plugin manifest's `dependencies` supports a minimum version, require branch-review ≥ 0.10.0.

### 3. implementation-manager: the skill and prompts

**Setup in integration mode** (added steps; the rest is unchanged):

1. **Choices.** If the owner hasn't said who merges the final PR, or whether to run a whole-feature review, ask in the reply text before anything else. Nothing is assumed. Setup continues on the owner's answer.
2. **CI check.** Read the repo's workflow triggers. If `pull_request` is filtered to branches that exclude the integration branch, tell the owner and stop: unit PRs would never get checks, so they would never be ready.
3. **Create the integration branch.** Create `integration/<slug>` from the current head of `<base>` on GitHub (`gh api repos/<repo>/git/refs`). Then `run.py init` with `--integration`. If the manager's planning PR (the `specs` unit) is already open against `<base>`, retarget it with `gh pr edit --base integration/<slug>`.
4. **Start the integration child** in the same first turn as the units, with the integration prompt below, and record its worktree. Its unit branch in `run.json` is the integration branch.
5. **Unit prompts.** Use the integration branch wherever they say `<base>`.
   - Sync means `git fetch origin && git merge origin/<integration branch>`, not `sync_with_base_branch`, which syncs with the app's base branch (`main`).
   - Their `/branch-review:review --base origin/<integration branch>`.
   - **Builder to verify:** whether `sync_with_base_branch` can target another branch, and whether a plain `git merge` in a child worktree hits sandbox-protected files. If so, use the tool with that branch, or note the limit.

**The integration child's prompt** (a new prompt alongside the unit prompt; it follows parts 8–10 of the unit prompt with the changes below):

- **Goal.** Own `integration/<slug>`: keep it current with `<base>` throughout the run, then deliver the final PR into `<base>`. Write no feature code except conflict resolutions and fixes from the whole-feature review.
- **Start.** In its worktree, switch to the integration branch (`git fetch origin && git switch integration/<slug>`). The app-made branch is left unused; this is the one child allowed a different branch, and its push requests name the integration branch.
- **Watch loop.** One background poll that ends on either of two events:
  - `<base>`'s remote head differs from what the integration branch last merged (`git ls-remote origin refs/heads/<base>`, every 60 s);
  - a `<name> final` or `<name> stop` line in `gates.log`.
- **On a `<base>` change:**
  1. `git fetch origin` and fast-forward to `origin/integration/<slug>`.
  2. Invoke `branch-review:record` on that commit, with the reason listing the unit PRs merged since the last record (`git log --merges --first-parent` or the PR numbers in commit subjects).
  3. `git merge origin/<base>`.
     - Clean: commit if needed and ask to push. It's covered.
     - Conflicted: resolve, commit, run `/branch-review:review --base origin/<base>` to convergence. It runs as a merge-check from the recorded round. Then ask to push.
  4. Start the watch loop again.
- **On `go`.** If `origin/integration/<slug>` moved past the merge's first parent (a unit merged meanwhile):
  1. Don't push.
  2. Reset the local branch to the remote (only its own unpushed merge is lost; `rerere` keeps resolutions).
  3. Record, merge again and ask again.
  
  Otherwise push HEAD to the integration branch. Never force-push.
- **On `final`:**
  1. Do one last `<base>` sync as above.
  2. If `feature_review` is yes, run `/branch-review:review --full --base origin/<base>` with the run's intent, to convergence. Fix findings on the integration branch, escalating as a unit would.
  3. Ask to push, then open the final PR into `<base>` and turn on Auto-fix.
  
  The manager passes the current `feature_review` value in the gate line or via `run.json`. The builder picks which; the child must not rely on the value from its first prompt, since `set` can change it.
- **After the final PR is open,** the watch loop keeps running until the final PR merges. It now also ends when the integration branch's remote head moves because a late unit merged.
  - **A `<base>` change:** sync as before. The push updates the final PR.
  - **A late unit merge:** fetch, then bring the final PR's head back under review. With `feature_review` yes, run `/branch-review:review` to convergence; it is a follow-up from the whole-feature review, so the late work becomes part of it. With no, record the new head. No push is needed, since the commit is already on the remote, and the manager's check on the final PR's head is then covered.
- **Status tokens.** As in part 9, plus `[watching]` while its poll runs with nothing to do.

**Event handling additions:**

- `SYNCED <unit> <sha>`: `run.py synced`, one line.
- The final gate: printed by `open-ready` after the last unit merges. Nothing to message; the child's poll reads it.
- With `final_merge: owner`, a `READY` for the final PR: tell the owner once, with the PR link, under "Waiting on you". Do not merge.
- **Finishing:** after the final PR merges, delete the integration branch (`gh api -X DELETE repos/<repo>/git/refs/heads/integration/<slug>`), then the existing finishing steps. The owner approved this deletion as part of the mode.

### 4. Tests and evals

- **branch-review unit tests** for `record`:
  - refused off the remote ref, with an empty reason, or outside a branch;
  - first record with an empty change set is saved;
  - no-op at the anchor's head;
  - after a record, a clean base merge is covered and a conflicted one chooses merge-check with only the resolution files;
  - recorded rounds excluded from the cap and second opinion;
  - summary wording.
- **implementation-manager unit tests:**
  - `init` and `set` validation;
  - integration-unit rules;
  - the final gate (once, only when every other unit is done or stopped, at least one done);
  - `SYNCED` and `synced`;
  - queue priority;
  - the owner-merges refusal;
  - `load` of an older run.
- **Eval:** the builder decides whether the integration child's record-then-merge sequence warrants an eval case, or whether unit tests plus the skill-text tests are enough. Record the choice in `docs/deferred` if no eval is added.

## Decided with the owner

- Integration branch, not gh-stack; the goal is only a clean `main`.
- Opt-in; the default is unchanged.
- `main`'s changes come into the integration branch as they land.
- A child session owns the integration branch: syncs, conflict resolutions, the whole-feature review if requested, and the final PR.
- Who merges the final PR has no default; the owner must choose.
- The whole-feature review is opt-in.
- branch-review gets a `record` step, trusted because each unit passed the merge bar and the step only accepts commits already on the remote.
- `record` is a back door for this protocol, not a user feature, guarded by `user-invocable: false`, its description and the remote-only rule.
- Approach A: the mode lives in the run script.
- Late work (units added after the final gate, cards, ORPHANs, approved fixes) goes into the integration branch, and the final PR waits until it lands.

## My calls (owner may revise)

- Branch name `integration/<slug>`, created on GitHub from `<base>`'s head at setup.
- Both choices asked at setup, before `init`; changeable with `set` until the final gate.
- The planning PR (`specs`) targets the integration branch.
- The final PR uses the run's merge method.
- If `<base>`'s protection blocks the final merge (approvals), the manager tells the owner rather than working around it.
- The integration branch is deleted after the final PR merges.
- Integration requests jump the queue.
- Slot release for direct pushes via a `SYNCED` event and a `synced` command.
- The integration child resets and redoes its sync when a unit merged under it, rather than merging the remote branch into its local merge.
- An owner-merges final PR is guarded by never reporting it `READY` (it gets `FINAL-READY`), not by a new merge command, so the default run's merge step is unchanged.
- An integration sync in flight blocks unit merges in both queue modes, rather than refusing integration mode with `--queue parallel`.
- After the final PR opens, late unit merges are brought under review by a follow-up review (feature review on) or a record (off).

## Left to the builder

- Whether "the units' base" is derived or stored in `run.json`.
- Whether `record` also checks the live remote.
- How the child learns the current `feature_review` value at the final gate.
- How the child extracts merged PR numbers for the record reason.
- The sync mechanism for unit children (see the verify note in section 3).
- Whether to add an eval case.
- Exact wording of new prompt parts, status lines and README sections.
