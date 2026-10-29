# branch-review

A local replacement for CodeRabbit pull request reviews. It reviews a branch before the branch becomes a pull request. A reviewer agent examines the full change set. Claude fixes the findings, and a new reviewer agent checks the fixes. The loop stops when the review is clean.

## Requirements

You must have `python3` 3.9 or later.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install branch-review@mbcrawfo-skills
```

## Review a branch

Complete the full change on the branch. Then run `/branch-review:review` from that branch:

```
/branch-review:review [--full] [--model NAME] [--base REF] [--intent TEXT | --intent-file PATH] [--paths GLOB...] [--generated GLOB...] [--second-opinion | --no-second-opinion] [--notes-file PATH] [--related-branch REF...] [--resolve ID NOTE]
```

The review keeps most options for later runs on the same branch. Each option below says when it does.

- `--full`: Do a deep review of the full change set, also when earlier rounds exist.
- `--model NAME`: The model for the reviewer agents: `opus`, `sonnet`, `haiku` or `fable`. By default, deep reviews use `opus` and short follow-up reviews use `sonnet`.
- `--base REF`: The branch that the change will merge into. Use it when the repo default branch is not correct. Later runs keep it.
- `--intent TEXT` or `--intent-file PATH`: The PR description. Say what the change is for, what it does, and what it does not do. Later runs keep it.
- `--paths GLOB...`: Review only the files that match. Use it for a change set that is too large for one review. A directory, or a pattern that ends in `/`, includes all files below it. `.` includes the full repo. A change to `--paths` causes a new deep review.
- `--generated GLOB...`: Patterns for more generated files. The review also uses files that `.gitattributes` marks `linguist-generated` or `-diff`. The reviewer still examines generated files, but they do not make a follow-up into a deep review. Later runs keep the patterns. `--generated .` removes them.
- `--second-opinion`: Do a second deep review when no findings are open. The review also does this automatically when the change has a high risk.
- `--no-second-opinion`: Do not do the automatic second review on this branch. Later runs keep this setting until you give `--second-opinion`.
- `--notes-file PATH`: Notes from earlier reviews of single tasks, for the full-branch review. Deep reviews examine each note. Later runs keep them. An empty file removes them.
- `--related-branch REF...`: Branches that make the same change in a different place, for example the same feature for a different engine. The reviewer compares the files. Later runs keep them. `--related-branch .` removes them.
- `--resolve ID NOTE`: Record your decision about a finding that the review sent to you. A note that asks for a fix opens the finding again. A note that says "fix it later" defers it. A note that says "keep it" accepts it.

## How a review runs

1. **Set up.** The review finds the earlier rounds of this branch. The first round is a deep review of the full change set. Later rounds examine only the new changes and the earlier findings.
2. **Round.** A new reviewer agent examines the change against your intent. It writes a report.
3. **Triage.** Claude examines each finding. Claude fixes it with a regression test, disputes it with evidence, or sends it to you. A finding that needs your choice always comes to you.
4. **Second opinion.** When the risk is high, a new reviewer examines the full change one more time.
5. **Report.** A summary shows the rounds, the merge risk and each finding. The agent that opens the PR adds a "Review notes" block to the PR description.

The loop continues until no findings are open and all fixes are verified. It stops after six rounds.

Run the review from the agent that owns the full change. For contributor details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Where state lives

The review keeps its state outside the repo, in the plugin data directory. Claude Code puts this directory at `~/.claude/plugins/data/<plugin-id>/`:

```
<owner__repo>/<branch>/
  review.json    # findings, status and round metadata
  rounds/<N>.md  # each reviewer's report
  summary.md     # the latest summary
  review-notes.md  # the Review notes block for the PR description
```

Each review run removes old state first. It removes the state of a branch that is not local and not on `origin`. It also removes state older than 30 days. It never removes the state of the current branch.

## Clean up

Use `/branch-review:cleanup` to see review state or to remove it:

```
/branch-review:cleanup [--branch | --repo | --all] [--older-than DAYS] [--yes]
```

- With no scope, it shows a table of the state that exists.
- `--branch`, `--repo` and `--all` select the current branch, the current repo, or all state. Give only one.
- `--older-than DAYS` selects only state older than DAYS days. It needs a scope.
- Before it removes state, it shows the list and asks you to confirm. `--yes` skips the question. It needs a scope.

## For other plugins

Agents in other plugins use these two skills. An example is the implementation manager, which must know that a commit has a review before a push.

### Check a commit

`/branch-review:check` tells if a clean review covers a commit. It prints `covered` or `not covered: <reasons>`. It does not change files or refs.

```
/branch-review:check --repo PATH [--commit SHA] [--state-dir PATH]
```

`--commit` is `HEAD` by default.

### Record commits already on the remote branch

`branch-review:record` marks a commit as recorded, without a review. Only the implementation-manager integration-branch process uses it. It accepts only a commit that is already on `origin/<branch>`, so it cannot approve local work. It does not show in the `/` menu.

```
branch-review:record --repo PATH --commit SHA --reason-file PATH [--base REF]
```

## Review guidance in AGENTS.md

The reviewer reads each `AGENTS.md` and `CLAUDE.md` from the repo root down to each changed file. Put review guidance in the `AGENTS.md` of your repo:

- The files that the reviewer must not review, for example generated code or vendored files.
- The documents that record intent and do not need updates, for example plans, specs and changelogs.
