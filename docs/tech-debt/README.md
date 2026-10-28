# Tech debt

Problems that were identified and not resolved, and intentional choices known to be sub-optimal.  Each active item is fully documented in its own file, linked from the ID column.  When an item is resolved, its row is removed and its file deleted in the same PR as the fix.

Next id: `TD-0008`

## Active items

| ID | Status | Added | Impact | Description |
|----|----|----|----|----|
| [TD-0001](TD-0001-implementation-manager-hold-labels.md) | Open | 2026-10-25 | Low | An implementation manager hold's label in `status` can name a question already answered, or miss a second one |
| [TD-0002](TD-0002-integration-merge-check-second-opinion.md) | Open | 2026-10-26 | Low | A critical or major finding in an integration branch's merge-check makes a whole-branch second opinion due, a whole-feature review the owner may not have asked for |
| [TD-0003](TD-0003-integration-final-gate-stopped-prereq.md) | Open | 2026-10-26 | Low | In integration-branch mode a unit gated on a stopped prerequisite never opens, so the final gate never opens and `status` does not say why |
| [TD-0005](TD-0005-agents-md-inventory-cli-contract.md) | Open | 2026-10-27 | Low | `agents_md_inventory.py` exits 2 with multi-line usage on argparse errors, and reads the rulings file through a symlink out of the repo |
| [TD-0006](TD-0006-agents-md-reviewer-unquoted-inputs.md) | Open | 2026-10-27 | Low | agents-md-cleanup passes `SCOPE` and `AGENTS_MD_DIRS` space-separated, and its reviewer runs `REPO`, `SCRIPT` and `FILE` unquoted, so paths with spaces break |
| [TD-0007](TD-0007-agents-md-cleanup-weak-checks.md) | Open | 2026-10-27 | Medium | agents-md-cleanup's change check uses porcelain status, project choices in bulk deletions rest on the reviewer alone, and the first-run `no-dispatch` grader can pass trivially |

## Columns

| Column | Contents |
|----|----|
| ID | `TD-NNNN`, linked to the item's file in this folder |
| Status | `Open` (nobody is working on it), `In progress`, or `Blocked` (waiting on something named in the item file) |
| Added | Date the item was recorded, `YYYY-MM-DD` |
| Impact | `Low` (cosmetic or local), `Medium` (slows work or risks defects), `High` (risks correctness, security or data) |
| Description | One line: what is wrong |
