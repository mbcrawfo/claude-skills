# implementation-manager

Completes a planned piece of work that has many phases, until all of its code is merged. The session where you run it becomes the manager. The manager starts one child session for each unit of work, for example a phase or a sub-phase. Each child delivers one pull request. The manager starts a unit only when the units it needs are merged. It merges each PR when the PR is ready.

## Requirements

- The [`branch-review`](../branch-review/README.md) plugin, version 0.12.1 or later. It installs with this plugin. The manager uses it to review each unit before a push or a merge.
- The Claude desktop app. The manager uses the session tools of the app to start and monitor child sessions. The skill does not work in a plain terminal session.
- `gh`, with a login to GitHub.
- `python3` 3.9 or later.

If a skill that the manager needs is missing, the manager stops at setup.

## Install

```
/plugin marketplace add mbcrawfo/claude-skills
/plugin install implementation-manager@mbcrawfo-skills
```

## Run it

Plan the work in a session first. Then ask that session to be the implementation manager. You can also start it with a spec, a plan or an epic:

```
/implementation-manager:implementation-manager [spec, plan or epic path]
```

When you start it, you let the manager start child sessions. You also let it merge the PRs of the run when they meet the merge rules below. You do not let it do other things.

During the run:

- Each child does the work for its unit and reviews it with `/branch-review:review`.
- A child must get approval from the manager before each push. The manager gives approval only when a clean review covers the commit.
- By default, only one child at a time can push to its PR. The other children wait in a queue. Tell the manager if you want parallel pushes.
- When a child has a question that only you can answer, the manager sends it to you. If the question blocks the merge, the PR of that unit does not merge until you answer. An escalated review finding always blocks the merge.
- At the end, the manager offers to archive the child sessions.

## Integration-branch mode

By default, each PR merges into the base branch when it is ready. Thus, the base branch can contain a part of the feature during the run. To keep the base branch clean, ask for integration-branch mode when you start the run. Each unit then merges into an `integration/<slug>` branch. One final PR moves the full feature into the base branch.

At setup, the manager asks you two questions:

- Who merges the final PR: the manager or you?
- Do you want a review of the full feature before the final PR?

The manager does not guess these answers. You can change them until the final PR starts.

This mode has these limits:

- The base branch must be the default branch of the repository.
- The `pull_request` triggers of your workflows must not exclude the integration branch.
- The merge method must be merge or squash. Rebase is not available.

If a limit is not met, the manager stops and tells you.

## Merge rules

The manager merges a PR only when all of these conditions are true:

- The PR is open, all its checks passed, and it has no unresolved review threads.
- A clean `branch-review` review covers the head commit of the PR.
- The child has no push request that waits for an answer, and no approved push that it did not do yet.
- No question that blocks the merge waits for an answer from you.
- All units that must merge first are merged.

For contributor details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Known issues

- In `status`, the hold label of a unit can show an old question. This occurs when you answer one question while a different question is still open. The hold still works correctly: the unit does not merge until all questions that block the merge have an answer. To see the open questions of a unit, look in `notes.md`.
- In integration-branch mode, a unit stays closed when a unit that it needs is stopped. Thus, the final PR does not start, and `status` does not tell you why. Stop that unit too, or remove the prerequisite. See [TD-0003](../../docs/tech-debt/TD-0003-integration-final-gate-stopped-prereq.md).
