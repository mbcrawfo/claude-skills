# branch-review

A local, agent-run replacement for CodeRabbit PR reviews that checks a branch before it becomes a pull request. A fresh reviewer agent reviews the whole change set, you fix what it finds, and a new reviewer checks the fixes until the review converges.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install branch-review@mbcrawfo-skills
```

## Review a branch

Run `/branch-review:review` from the branch once the whole change is complete:

```
/branch-review:review [--full] [--model NAME] [--base REF] [--intent TEXT | --intent-file PATH] [--paths GLOB...] [--resolve ID NOTE]
```

- `--full`: force a deep review of the whole change set, even if earlier rounds exist.
- `--model NAME`: model for the reviewer agents, as an Agent-tool model name (`opus`, `sonnet`, `haiku` or `fable`). Defaults to `opus` for a deep round and `sonnet` for a follow-up.
- `--base REF`: the ref the change will merge into, overriding the detected default branch. Later runs reuse the stored base.
- `--intent TEXT` or `--intent-file PATH`: the PR description, meaning what the change is for, what it does and what it deliberately does not do. Later runs reuse the intent stored with the review.
- `--paths GLOB...`: limit the review to matching files, useful for a change set too large to review at once. A directory, or a pattern ending in `/`, means everything under it; `.` means the whole repo. Changing `--paths` between runs forces a deep review, so no file in the new scope goes unreviewed.
- `--resolve ID NOTE`: record your decision on an escalated finding. A note that asks for a fix reopens the finding; one that says to leave it defers it.
- `--state-dir PATH`: keep state in this absolute directory instead of the plugin data directory. Meant for test harnesses; give `/branch-review:cleanup` the same value.

The loop:

- **Set up.** The state script prunes old state, then locates this branch's earlier recorded rounds. The first review is deep; a later run on a branch that builds on an earlier round is a follow-up. These force a deep review instead, with open findings carried forward for the reviewer to verify:
  - `snapshot-missing` or `history-rewritten`: a rebase, amend or reset that loses the earlier snapshot or rewrites history;
  - `base-moved`: the merge base with the base branch changed, usually because the base was merged in, so a delta would show upstream code as the change;
  - `paths-changed`: `--paths` differs from the earlier round's.

  A round whose reviewer never reported is not recorded and never anchors a follow-up, and a round with no changes is never saved.
- **Round.** The branch's working tree is snapshotted, and a fresh `branch-review:reviewer` agent reviews the change set against your intent, or verifies the earlier findings and the new delta in a follow-up.
- **Record and triage.** The reviewer's findings are recorded. For each open one, the orchestrating agent checks the claim against the code, then fixes it with a regression test, disputes it with evidence, or escalates it for a decision.
- **Repeat.** Each round gets a new reviewer. The loop continues while findings are open, fixes are unverified or new findings appeared, and stops when the state script reports convergence or after six rounds in one run.
- **Report.** A summary lists findings still open or unverified, then escalated, deferred and invalid ones. Copy the escalated, deferred and invalid sections into the PR description under a "Review notes" heading.

Notes, discussion entries and decisions reach the state script through files (`--note-file`, `--text-file`) rather than quoted shell arguments, since reviewer text can contain quotes, `$` and backticks.

Run the review from the agent that owns the whole change. An implementer working one slice of a larger plan should hand the review to its orchestrator.

## Where state lives

State is kept outside the repo, in the plugin's data directory, which Claude Code resolves to `~/.claude/plugins/data/<plugin-id>/`:

```
<owner__repo>/<branch>/
  review.json    # findings, status and round metadata
  rounds/<N>.md  # each reviewer's report
  summary.md     # the latest summary
```

Slashes in the branch name become `__`, and every path segment of the origin URL is kept, so a GitLab subgroup repo is `group__sub__repo`. A repo without an origin URL gets a `local-<hash>` key that its linked worktrees share. A detached HEAD is `detached-<sha7>`.

Every review run prunes stale state first. It removes the state of branches that exist neither locally nor as `origin/<branch>`, and state untouched for more than 30 days. Detached-HEAD state matches no branch, so it goes only by age. It never removes the current branch's state.

The state script refuses an empty or relative `--state-dir`, so an unset `CLAUDE_PLUGIN_DATA` is an error rather than the current directory; it also refuses the filesystem root and your home directory itself. Pruning and cleanup only remove `<repo>/<branch>` directories that hold `review.json` or `rounds/`; any other directory under the state dir, and any symlink (including a symlinked repo directory, which is never walked into), is reported as `skipped` and left alone. A directory that cannot be checked or removed is reported under `errors` without stopping the rest, and the command then exits 1.

## Clean up

`/branch-review:cleanup` lists review state, or removes some of it:

```
/branch-review:cleanup [--branch | --repo | --all] [--older-than DAYS] [--yes]
```

With no scope it prints a table of what exists. `--branch` and `--repo` target the current branch or repository, `--all` everything. `--older-than DAYS` narrows that scope to state updated more than DAYS days ago; state whose age cannot be read is kept. It shows what it would remove and asks for confirmation unless you pass `--yes`. `--older-than` and `--yes` need a scope, and only one scope may be given.

## Review guidance in AGENTS.md

The reviewer reads every `AGENTS.md` and `CLAUDE.md` from the repo root down to each changed file. Put review-specific guidance in the repo's `AGENTS.md`: what the reviewer should not review (generated code, vendored files), and which documents are records of intent rather than documentation to keep current (plans, specs, changelogs).

## Tests and evals

```bash
python3 -m unittest discover -s plugins/branch-review/tests -v
```

The review-loop eval lives in `evals/review-loop/`. Its scaffold script builds a small Python repo with four planted defects in `repo/` inside the run's empty workspace, plus an empty `state/` directory next to it, so the run needs `--scaffold`. The eval sandbox confines writes to the workspace, so the prompt tells the agent to pass `--state-dir` pointing at that `state/` directory. The agent writes the review summary to `review-summary.md` in the workspace, which the `loop` grader reads. Run it from this plugin's directory, with a credential the sandbox can use (`claude setup-token` and `CLAUDE_CODE_OAUTH_TOKEN`, since the sandbox cannot read the Keychain login):

```bash
claude plugin eval . --case review-loop --runs 1 --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch
```

`tests/fixture/make_fixture.sh <dir>` builds the same repo for a manual run.

Results go to `evals/results/` (under the plugin, or under the repo root when the eval is run from there); both are gitignored.
