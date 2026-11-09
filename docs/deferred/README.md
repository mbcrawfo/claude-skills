# Deferred work

This folder records planned work that a PR did not deliver as planned. Each active item has its own file, and the ID column links to it. When a PR completes an item, that PR removes the row and deletes the file.

Next id: `D-0005`

## Active items

| ID | Status | Added | Planned in | Description |
|----|----|----|----|----|
| [D-0002](D-0002-implementation-manager-after-eval-grader.md) | Open | 2026-10-25 | [Implementation manager phase 8 review design](../superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md) | An eval grader or case for a unit refused the push slot by `--after`, including a slot holder that gains `--after` |
| [D-0003](D-0003-implementation-manager-integration-eval.md) | Open | 2026-10-26 | [Implementation manager integration-branch mode design](../superpowers/specs/2026-10-26-implementation-manager-integration-branch-design.md) | An eval case for integration-branch mode: the integration child's record, base merge, and reset-and-redo |
| [D-0004](D-0004-branch-review-split-deep-round.md) | Open | 2026-11-09 | [Branch-review split deep round design](../superpowers/specs/2026-11-08-branch-review-split-deep-round-design.md) | The split deep round (Phase 2), its `deep_round` option and the second-opinion decision (Phase 3); only the Phase 1 usage report and small replay are delivered |

## Columns

| Column | Contents |
|----|----|
| ID | `D-NNNN`, linked to the item's file in this folder |
| Status | `Open` (nobody works on it), `In progress`, or `Blocked` (it waits for something that the item file names) |
| Added | The date when the item was added, `YYYY-MM-DD` |
| Planned in | Link to the spec, plan or doc that called for the work |
| Description | One line: what is missing |
