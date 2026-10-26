# Deferred work

Planned work that has not been delivered as planned.  Each active item is fully documented in its own file, linked from the ID column.  When an item is resolved, its row is removed and its file deleted in the same PR as the fix.

Next id: `D-0003`

## Active items

| ID | Status | Added | Planned in | Description |
|----|----|----|----|----|
| [D-0001](D-0001-implementation-manager-large-runs.md) | Open | 2026-10-25 | [Implementation manager phase 8 review design](../superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md) | An implementation manager that stays within its context on a run of more than about 10 units, for example by handing off to a fresh manager session |
| [D-0002](D-0002-implementation-manager-after-eval-grader.md) | Open | 2026-10-25 | [Implementation manager phase 8 review design](../superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md) | An eval grader or case for a unit refused the push slot by `--after`, including a slot holder that gains `--after` |

## Columns

| Column | Contents |
|----|----|
| ID | `D-NNNN`, linked to the item's file in this folder |
| Status | `Open` (nobody is working on it), `In progress`, or `Blocked` (waiting on something named in the item file) |
| Added | Date the item was recorded, `YYYY-MM-DD` |
| Planned in | Link to the spec, plan or doc that called for the work |
| Description | One line: what is missing |
