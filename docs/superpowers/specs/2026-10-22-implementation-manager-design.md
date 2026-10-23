# Implementation manager plugin: design

Date: 2026-10-22. Status: draft for approval.

## Intent

The `implementation-manager` skill runs planned multi-unit work (phases or sub-phases) to merged code through child sessions, one PR per unit. It lives in Michael's user profile (`~/.claude/skills/implementation-manager/`: `SKILL.md` and `scripts/wait-pr-ready.sh`) and is built around CodeRabbit: a CodeRabbit brief copied into every child, a separate CodeRabbit manager session that clears each push, a merge bar that waits for CodeRabbit's review, and a watch script that reads CodeRabbit's status.

CodeRabbit is being disabled. Its replacement is this repo's `branch-review` plugin, a local review loop. This work moves the skill into the repo as its own plugin and rebuilds its review gate on branch-review: children review their own work with `/branch-review:review`, and the manager independently confirms the review is clean for the exact commit before the child may open a PR or push again. A PR merges once its build passes and the review still covers its head.

## Goals

- The skill ships from this marketplace as the `implementation-manager` plugin, replacing the user-profile copy.
- Every unit's code is reviewed by branch-review, with every finding addressed, before its PR opens.
- The manager, not the child, decides whether a push is allowed, from branch-review's own state, not from the child's report.
- Every push after the PR opens (CI fixes, review fixes, updates from the base branch) passes the same check first.
- The manager merges a PR when CI passes and the review covers the PR's head commit.
- Escalated review findings reach the user, through the manager, before the PR can open.
- Updating a branch from its base no longer forces a full deep review in branch-review when the update merged cleanly.

## Non-goals

- No CodeRabbit support of any kind. Repos running this will have CodeRabbit disabled.
- No hook or other mechanical push blocking (considered and rejected; see Approaches).
- No changes to how units are broken down, gated, started or tracked beyond what the review gate needs.
- No changes to branch-review's review loop itself (reviewer, triage, dispute, convergence), other than the base-moved behavior and the new coverage check.

## Anti-goals

- Unreviewed code reaching the base branch. Whatever a child does, the merge-time check must stop it.
- The manager trusting a child's claim ("review is clean") instead of checking state itself.
- A full Opus re-review of the whole change every time a sibling unit merges and the child syncs with the base.
- Spending the 10-message budget per session on push permissions. Permissions go through a file, like gates.

## Constraints

Carried over from the current skill, unchanged:

- Sessions start only on the user's own turn, so every child starts in the first turn; units with unmerged prerequisites wait on a gate file.
- The app stops delivering a session's messages after 10 sends without the user typing in it. Coordination is through files in the run folder.
- Nothing notifies the manager when a PR is ready or merged; a background watch script per open PR is its signal. A child's end-of-turn note does reach the manager (first 400 characters of its recap).

New:

- branch-review's state lives in branch-review's plugin data directory (`~/.claude/plugins/data/<branch-review plugin id>/<owner__repo>/<branch>/review.json`), keyed by origin URL and branch name. Children run in worktrees on the same machine, so the manager can read a child's state. The manager's own `CLAUDE_PLUGIN_DATA` is implementation-manager's, not branch-review's, so it cannot find branch-review's script or state on its own; see "Coverage check" for how it gets there.
- Repo conventions (AGENTS.md): Python 3.9+, standard library only; tests under `plugins/<name>/tests/`; ruff; bump plugin versions; validate with `claude plugin validate`; a plugin's main workflow skill ships an eval case.
- The repo is public. Nothing from private repos goes into the skill, examples or fixtures.
- Skill files must never contain a dollar sign followed by a digit (Claude Code substitutes skill arguments).

## Approaches considered

How "the child may not push without the manager's permission" is enforced. In every option the manager runs the check and writes its permission to a run-folder file.

1. **Prompt-enforced permission file (chosen).** The child prompt says to open a PR or push only after the permission file has a `go` line for its current commit, and states explicitly that an Auto-fix wake-up is never permission to push. This is the mechanism the CodeRabbit manager skill uses, and it has worked well. A child that pushes anyway cannot get unreviewed code merged, because the merge bar re-runs the coverage check. Smallest build.
2. **Permission file plus a push-guard hook.** A PreToolUse hook blocks `git push` and `gh pr create` in a run's child unless the commit is cleared. Mechanical enforcement, but adds a hook script and tests, and the hook must stay inert in every non-run session, including Michael's own work. Rejected: the merge-time check already protects the base branch, and the proven prompt mechanism is enough.
3. **Hook runs the coverage check itself, no manager round trip.** Faster, but the manager would not be the one validating, which is the point of the request. Dropped.

Packaging: a new `implementation-manager` plugin rather than a skill inside `branch-review`, so the general review tool is not tied to one orchestration workflow. It requires branch-review to be installed.

## Decided design

### Plugin layout

```
plugins/implementation-manager/
  .claude-plugin/plugin.json        # version 0.1.0
  README.md
  skills/implementation-manager/SKILL.md
  scripts/wait-pr-ready.sh          # moved from the profile, CodeRabbit removed
  evals/<case>/...                  # see "Left to the builder"
```

Plus an entry in `.claude-plugin/marketplace.json` and a line in the root README's plugin list. The skill references its script as `${CLAUDE_SKILL_DIR}/../../scripts/wait-pr-ready.sh`.

Start from the current profile `SKILL.md` and script; keep everything not named here (setup, gates, starting children, notes, tracker, part k of N, finishing, red flags) and remove every CodeRabbit element: the argument for the CodeRabbit manager link, setup step 3 (the brief), `coderabbit-brief.md`, the brief-refresh step at the top of event handling, child prompt part 10, the `coderabbit-skipped` event, `REQUIRE_CODERABBIT`, and CodeRabbit lines in the merge bar and red flags.

### branch-review change 1: clean base updates stay follow-ups

Today `choose_mode` returns `deep`/`base-moved` whenever the merge base with the base branch differs from the anchor round's. New behavior when the merge base moved:

1. Simulate merging the anchor round's reviewed snapshot onto the new merge base: `git merge-tree --write-tree --merge-base=<anchor merge_base> <anchor snapshot> <new merge base>`.
2. **Clean** (exit 0): mode is `followup`, reason `base-updated`. The round's `diff_from` is the simulated merge result (wrapped in a commit so it can be stored and diffed like a snapshot), and `diff_to` is the new snapshot. The diff then holds only the branch's own work since the anchor, with none of the base's code. If the update was all that changed, the diff is empty, `begin-round` reports `empty`, and the review stays converged with no new round.
3. **Conflicts**, or a git too old for `merge-tree --write-tree --merge-base` (git 2.40+): mode is `deep`, reason `base-moved`, as today. Conflict resolutions are unreviewed code; this case is rare. (Builder's note: this fallback is Claude's call, not Michael's.)

The new round records the new merge base, so later rounds anchor on it normally. Update the review skill's mode list, the README and tests (clean update with no other change, clean update plus new commits, conflicting update).

### branch-review change 2: coverage check

A new state-script subcommand, `covered`, answers one question: is commit `<sha>` covered by a clean review of this branch? It takes `--commit <sha>` (default `HEAD`) and the usual `--state-dir`/`--repo`, and judges the commit, never the working tree. Covered when all of these hold:

- at least one recorded round exists;
- no finding is `open`, `still-open`, `fixed` (unverified) or `escalated` (waiting on the user);
- the latest round reviewed the whole change, not a `--paths` subset (Claude's call: a partial review never clears a push);
- the latest round's snapshot is the commit's tree, or differs from it only by a clean update from the base (change 1's simulated merge, diffed against the commit, is empty).

There is deliberately no "the latest round added no findings" rule (Claude's call). When a round's findings are all closed without a code change (disputed to `invalid`, or escalated and then deferred), the next follow-up has an empty diff, `begin-round` never saves it, and the latest recorded round keeps its `added` count forever; such a rule would refuse that branch permanently. The rules above already cover it: any finding fixed in code stays `fixed` until a later round verifies it, and any code change after the last round fails the snapshot rule.

A first deep round with no findings counts as clean; no follow-up round is needed, since it would have nothing to review.

Output is one JSON object: `covered` (bool), `commit`, `reasons` (list, empty when covered; e.g. `no-rounds`, `open-findings`, `unverified-fixes`, `escalated`, `changes-since-review`, `partial-scope`, `base-conflict`), plus the counts. Exit 0 either way; exit 1 only for errors.

A small new skill, `branch-review:check`, wraps the subcommand so another plugin's agent can run it with branch-review's script path and data directory: arguments `--repo <path> --commit <sha> [--state-dir PATH]`, prints the JSON verdict. `--state-dir` passes through for test harnesses, as in the review skill. The manager runs it against the child's worktree path (from `get_session`), which carries the child's branch and origin.

The check catches mistakes and skipped steps, not a child working against the rules: the child writes its own ledger, and the manager does not cross-check `--resolve` decisions against the answers it forwarded (Claude's call).

### Child flow

The child prompt keeps its current parts minus CodeRabbit, with these changes:

- **Review.** When the unit's change is complete and its tests pass, run `/branch-review:review` with the unit's intent (the spec's goal, which becomes the PR description), and continue until it converges. The child is the agent that owns the whole change, so it runs the review itself; subagents implementing slices do not.
- **Escalations.** For each escalated finding, message the manager with the question, the finding id and the child's recommendation (this counts as a "decision the owner must make"). When the manager forwards the user's answer, record it with `--resolve <id>` using the user's quoted words, then re-run the review to convergence.
- **Asking to push.** Before opening the PR and before every later push: commit everything, sync with the base (ccd_host `sync_with_base_branch`), run the review if anything but a clean base update changed since it last converged, then start the permission poll with Bash `run_in_background` and end the turn with the status token `[push-request] <full sha>`. The poll waits for a line for that sha in `<run>/pushes.log`.
- **On `go <sha>`:** push exactly that commit (open the PR the first time). If HEAD has moved since, do not push; ask again.
- **On `redo <sha> <reason>`:** fix what the reason says (usually: run the review), then ask again with the new sha.
- **Explicit rule:** an Auto-fix wake-up (CI failure, review comment, conflict) is never permission to push. Fix locally, commit, and ask.
- Status tokens become `[gated]`, `[building]`, `[reviewing]`, `[fixing]`, `[push-request] <sha>`, `[waiting]`, `[blocked]`, `[review]` (PR open, nothing left to do unless CI or a reviewer reports something).
- PR description: the review summary's Escalated, Deferred and Invalid sections under "Review notes", as branch-review's report step says.

### Manager flow

New run-folder file: `pushes.log`, append-only lines `<unit> go <sha>` or `<unit> redo <sha> <reason>`; the last line for a unit and sha wins. Written by the manager only.

New and changed event rows:

| Event | Do |
|---|---|
| Child note begins `[push-request] <sha>` | Run `branch-review:check` on the child's worktree for that sha. Covered: append `go`. Not covered: append `redo` with the reasons. One short line in reply. |
| Child message with an escalated finding | Relay to the user like any question: the finding, the child's recommendation, Claude's recommendation. Forward the answer in one message, quoting the user. |
| Watch: `ATTENTION <pr> <sha> behind-base` | Send the child one message: sync with the base and ask to push. Restart the watch with `SKIP_HEAD`. |

The `coderabbit-skipped` row and the brief-refresh step go away.

**Merge bar,** all of:

- the watch printed `READY` for that head (or `ONCE=1` did): open, not draft, mergeable, at least one check and every check passed, no unresolved review thread, same on two polls;
- `get_session` shows the child idle (`isRunning: false`) with its latest note beginning `[review]`;
- `branch-review:check` on the PR's head commit says covered.

Merge with `--match-head-commit <sha>` as today.

### The manager's own planning PR (`specs` unit)

Michael's decision: the manager's own PR goes through the same gate. Before opening it and before every later push, the manager runs `/branch-review:review` on its own branch to convergence (escalations go straight to the user), then runs `branch-review:check` on the commit and pushes only when covered. Its merge bar is the watch's `READY`, no unpushed fix, and `branch-review:check` covered on the PR's head. This replaces the old CodeRabbit-specific `specs` lines in the merge bar.

### Removing the profile copies

Michael's decision: once the plugin is installed and no run is still going under the old skill, delete `~/.claude/skills/implementation-manager/` and `~/.claude/skills/coderabbit-manager/`. This is the last step of the work, done outside the repo, after confirming with Michael that no run is in progress. Leave the data folders (`~/.claude/implementation-manager/`, `~/.claude/coderabbit-manager/`) alone.

### Watch script

Port `wait-pr-ready.sh` to Python as `scripts/wait_pr_ready.py` (Claude's call: AGENTS.md says plugin scripts are Python 3.9+, standard library only, with tests). It still shells out to `gh`. Same arguments, environment variables and output lines, minus CodeRabbit: drop the CodeRabbit status fields, the `coderabbit-skipped` attention reason, `REQUIRE_CODERABBIT` and CodeRabbit wording. Everything else stays: READY, ATTENTION behind-base, MERGED/CLOSED, STALLED, ERROR, `SKIP_HEAD`, `ONCE`, `POLL_SECONDS`. Unresolved human review threads still block READY. Tests under `plugins/implementation-manager/tests/` drive it with a fake `gh` on `PATH` and a short poll interval.

### Eval

The eval sandbox has no session tools, no `gh` and no PRs, so the eval covers the manager's review gate only (Claude's call): a scaffolded fixture repo with prepared branch-review state in the workspace, a run folder with a units table, and a prompt delivering a child's `[push-request] <sha>` note. Graders check that the manager ran `branch-review:check` (with `--state-dir` pointing at the workspace state) and appended the right `go` or `redo` line to `pushes.log`, across a covered and a not-covered case. If the eval cannot load branch-review alongside implementation-manager, the builder stops and reports it rather than faking the check.

### Run folders

Unchanged: `~/.claude/implementation-manager/<owner>-<repo>/<run>/`.

## Left to the builder

- Exact wording of the skill and child prompt, within the decisions above.
- How the simulated merge is stored for `diff_from` (a commit object via `commit-tree`, like dirty snapshots) and how `covered` shares that logic with `choose_mode`.
- Test structure for both branch-review changes, using the existing fixture helpers.
- branch-review version bump (to 0.7.0) and README updates for `base-updated`, `covered` and `branch-review:check`.
- The eval's fixture and grader details, within "Eval" above.
