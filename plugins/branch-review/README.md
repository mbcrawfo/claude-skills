# branch-review

A local, agent-run replacement for CodeRabbit PR reviews. It reviews a branch before it becomes a pull request, using Claude Code agents instead of a hosted service. A fresh reviewer agent reviews the whole change set, you fix what it finds, and a new reviewer checks the fixes until the review converges.

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
- `--model NAME`: model for the reviewer agents. Defaults to `opus` for a deep round and `sonnet` for a follow-up.
- `--base REF`: the ref the change will merge into, overriding the detected default branch.
- `--intent TEXT` or `--intent-file PATH`: the PR description, meaning what the change is for, what it does and what it deliberately does not do. Later runs reuse the intent stored with the review.
- `--paths GLOB...`: limit the review to matching files, useful for a change set too large to review at once.
- `--resolve ID NOTE`: record your decision on an escalated finding. A note that asks for a fix reopens the finding; one that says to leave it defers it.

The loop:

- **Set up.** The state script prunes old state, then locates this branch's earlier rounds. The first review is deep; a later run on a branch that builds on an earlier round is a follow-up. A rebase or amend that drops the earlier snapshot forces a deep review, with open findings carried forward.
- **Round.** The branch's working tree is snapshotted, and a fresh `branch-review:reviewer` agent reviews the change set against your intent, or verifies the earlier findings and the new delta in a follow-up.
- **Record and triage.** The reviewer's findings are recorded. For each open one, the orchestrating agent checks the claim against the code, then fixes it with a regression test, disputes it with evidence, or escalates it for a decision.
- **Repeat.** Each round gets a new reviewer. The loop continues while findings are open, fixes are unverified or new findings appeared, and stops when the state script reports convergence or after six rounds in one run.
- **Report.** A summary lists escalated, deferred and invalid findings. Copy those sections into the PR description under a "Review notes" heading.

Run the review from the agent that owns the whole change. An implementer working one slice of a larger plan should hand the review to its orchestrator.

## Where state lives

State is kept outside the repo, in the plugin's data directory, which Claude Code resolves to `~/.claude/plugins/data/<plugin-id>/`:

```
<owner__repo>/<branch>/
  review.json    # findings, status and round metadata
  rounds/<N>.md  # each reviewer's report
  summary.md     # the latest summary
```

Slashes in the branch name become `__`.

Every review run prunes stale state first. It removes the state of branches that exist neither locally nor as `origin/<branch>`, and state untouched for more than 30 days. It never removes the current branch's state.

## Clean up

`/branch-review:cleanup` lists review state, or removes some of it:

```
/branch-review:cleanup [--branch | --repo | --all] [--older-than DAYS] [--yes]
```

With no scope it prints a table of what exists. `--branch` and `--repo` target the current branch or repository, `--all` everything. It shows what it would remove and asks for confirmation unless you pass `--yes`.

## Review guidance in AGENTS.md

The reviewer reads every `AGENTS.md` and `CLAUDE.md` from the repo root down to each changed file. Put review-specific guidance in the repo's `AGENTS.md`: what the reviewer should not review (generated code, vendored files), and which documents are records of intent rather than documentation to keep current (plans, specs, changelogs).

## Tests and evals

```bash
python3 -m unittest discover -s plugins/branch-review/tests -v
```

The review-loop eval lives in `evals/review-loop/`. Run it from this plugin's directory:

```bash
claude plugin eval . --case review-loop --runs 1 --allow-tools Bash Write Edit
```

Results go to `evals/results/`, which is gitignored.
