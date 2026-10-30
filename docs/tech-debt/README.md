# Tech debt

This folder records known problems that are not fixed, and intentional choices that are known to be sub-optimal. Each active item has its own file, and the ID column links to it. When a PR fixes an item, that PR removes the row and deletes the file.

Next id: `TD-0011`

## Active items

| ID | Status | Added | Impact | Description |
|----|----|----|----|----|
| [TD-0001](TD-0001-implementation-manager-hold-labels.md) | Open | 2026-10-25 | Low | An implementation manager hold's label in `status` can name a question already answered, or miss a second one |
| [TD-0002](TD-0002-integration-merge-check-second-opinion.md) | Open | 2026-10-26 | Low | A critical or major finding in an integration branch's merge-check makes a whole-branch second opinion due, a whole-feature review the owner may not have asked for |
| [TD-0003](TD-0003-integration-final-gate-stopped-prereq.md) | Open | 2026-10-26 | Low | In integration-branch mode a unit gated on a stopped prerequisite never opens, so the final gate never opens and `status` does not say why |
| [TD-0008](TD-0008-agents-md-cleanup-known-limits.md) | Open | 2026-10-27 | Low | agents-md-cleanup stops when `.claude` is a symlink, its project-choice check is coarse, it doesn't merge hard-linked AGENTS.md files, only SKILL.md gives a move target its resolved path, and a broken `.git` file blocks move targets below it |
| [TD-0009](TD-0009-agents-md-cleanup-doc-destinations.md) | Open | 2026-10-28 | Low | agents-md-cleanup's doc page choice, root `references` seed, output-read test detection, over-limit re-dispatch, unchecked user splits, unrecorded over-limit files, word-count scanner edge cases, missing size tests, the uncapped `(whole)` fix, an overlapping eval statement and `_docs` cap are known gaps |
| [TD-0010](TD-0010-implementation-manager-owner-queue-known-gaps.md) | Open | 2026-10-30 | Low | implementation-manager owner-queue known gaps: duplicated ref scope and id formatting, quiet `item drop` and `stop`, loose refusal tests, skill rules that disagree or need an extra `status`, and a thin `owner-queue` eval |

## Columns

| Column | Contents |
|----|----|
| ID | `TD-NNNN`, linked to the item's file in this folder |
| Status | `Open` (nobody works on it), `In progress`, or `Blocked` (it waits for something that the item file names) |
| Added | The date when the item was added, `YYYY-MM-DD` |
| Impact | `Low` (cosmetic or local), `Medium` (makes work slower or can cause defects), `High` (can cause incorrect results, security problems or data loss) |
| Description | One line: what is wrong |
