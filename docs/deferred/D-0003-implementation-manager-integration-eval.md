# D-0003: Implementation manager eval for integration-branch mode

## Planned

The [integration-branch mode design](../superpowers/specs/2026-10-26-implementation-manager-integration-branch-design.md) ("Tests and evals") left it to the builder whether the integration child's record-then-merge sequence warrants an eval case, and asked for the choice to be recorded here if none was added. [AGENTS.md](../../AGENTS.md) asks for a plugin's main workflow skill to ship eval cases.

## Delivered instead

No eval case for integration-branch mode in implementation-manager 0.5.0. The mode is covered by unit tests and the skill text:

- The run script: [`test_runstate.py`](../../plugins/implementation-manager/tests/test_runstate.py), [`test_watcher.py`](../../plugins/implementation-manager/tests/test_watcher.py), [`test_prstate.py`](../../plugins/implementation-manager/tests/test_prstate.py) and [`test_run_cli.py`](../../plugins/implementation-manager/tests/test_run_cli.py): the `init` and `set` options, the integration unit, the final gate, `SYNCED` and `synced`, queue priority, the merge blocks and `FINAL-READY`.
- branch-review's `record`: [`plugins/branch-review/tests/`](../../plugins/branch-review/tests/), including coverage of a clean base merge after a record and a merge-check limited to the resolutions after a conflicted one.
- The protocol: "Integration-branch mode" and "The integration child" in [`SKILL.md`](../../plugins/implementation-manager/skills/implementation-manager/SKILL.md).

Nothing tests that a manager follows the integration setup steps, or that an integration child records, merges, resets and redoes as the prompt says.

## Why deferred

An eval needs a scaffold with a remote and two branches (the base and `integration/<slug>`), with unit PRs merged into the integration branch and the base moving under it. The eval sandbox setup has no such scaffold yet (2026-10-26).

## Remaining work

Add a case under [`plugins/implementation-manager/evals/`](../../plugins/implementation-manager/evals/):

- In `scaffold.sh`, create a bare remote, push `main` and `integration/<slug>` to it, merge one unit commit into the integration branch with a `(#N)` subject, and add a commit to `main` after that. Initialise a run with `run.py init … --integration integration/<slug> --final-merge owner --feature-review no` and an integration unit with its worktree on the integration branch.
- In the prompt, have the session act as the integration child on a `base <sha>` wake-up.
- Graders: a `tool_used` grader for `branch-review:record` before the base merge, with a reason naming the merged PR; a grader that the merge used `base: main`; a file grader on `requests.log` for a push request naming `integration/<slug>`; and, in a second step where the remote integration branch moved after the merge, a grader for `git reset --hard origin/integration/<slug>` and no force-push.
- The reset step should also cover a fix commit the child made after its merge: grade that the fix is cherry-picked back (`git cherry-pick -x`) and survives the reset.
- Run the case with `claude plugin eval` and record the result.

## Trigger

The eval sandbox gaining a scaffold with a remote, a reported integration-mode run that went wrong, or the next change to the integration child's prompt.
