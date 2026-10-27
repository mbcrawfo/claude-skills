# TD-0002: An integration merge-check finding makes a whole-branch second opinion due

## Problem

In integration-branch mode the integration child merges the base into `integration/<slug>`. When that merge conflicts, its `/branch-review:review` runs a merge-check round that sees only the resolutions and the files both sides changed (see "The integration child" in [`SKILL.md`](../../plugins/implementation-manager/skills/implementation-manager/SKILL.md)). If that round finds a critical or major issue, branch-review's automatic second opinion becomes due (`_second_opinion_triggers` in [`review_state.py`](../../plugins/branch-review/scripts/review_state.py), reason `major-finding`), and `branch-review:check` reports `second-opinion-pending` until it runs.

A second opinion is a deep review of the whole branch against `main`. On the integration branch that is the whole feature so far: a whole-feature review the owner may not have asked for (`--feature-review no`), run in the middle of the run. Since branch-review 0.11 a due second opinion can also replace the merge-check round itself, so the whole-branch review is what checks the resolutions.

## Why it exists

Such findings should be rare: the merge-check reviews only conflict resolutions, and each unit's work was reviewed on its own branch. A second opinion is also the safe direction: it reviews more, never less. Scoping the trigger would need branch-review to tell recorded deltas from reviewed ones when it decides a second opinion, a change to a plugin that otherwise knows nothing about this protocol.

## Impact

Low. When it happens, one sync takes much longer and costs a deep review of the whole integration branch. Since implementation-manager 0.6.0 that review runs under the integration child's `syncing` hold, so every unit merge waits for it, and the watcher reports `STUCK <unit> - syncing` after an hour (the manager does nothing while the child is still reviewing). Nothing merges with less review than intended.

## Proposed fix

Scope the second-opinion triggers to findings in non-recorded deltas: in `_second_opinion_triggers`, a `major-finding` from a merge-check round whose anchor is a recorded round makes a second opinion due over that round's own scope (the resolutions and overlapping files) rather than a deep review of the whole branch. Add a test in `plugins/branch-review/tests/` with a recorded round, a conflicted base merge and a major finding in the merge-check.

## Trigger

A real run where an integration merge-check finding triggered a whole-branch second opinion, or the next change to second-opinion triggers.
