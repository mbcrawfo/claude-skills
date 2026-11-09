# Tech debt

This folder records known problems that are not fixed, and intentional choices that are known to be sub-optimal. Each active item has its own file, and the ID column links to it. When a PR fixes an item, that PR removes the row and deletes the file.

Next id: `TD-0017`

## Active items

| ID | Status | Added | Impact | Description |
|----|----|----|----|----|
| [TD-0001](TD-0001-implementation-manager-hold-labels.md) | Open | 2026-10-25 | Low | An implementation manager hold's label in `status` can name a question already answered, or miss a second one |
| [TD-0002](TD-0002-integration-merge-check-second-opinion.md) | Open | 2026-10-26 | Low | A critical or major finding in an integration branch's merge-check makes a whole-branch second opinion due, a whole-feature review the owner may not have asked for |
| [TD-0003](TD-0003-integration-final-gate-stopped-prereq.md) | Open | 2026-10-26 | Low | In integration-branch mode a unit gated on a stopped prerequisite never opens, so the final gate never opens and `status` does not say why |
| [TD-0008](TD-0008-agents-md-cleanup-known-limits.md) | Open | 2026-10-27 | Low | agents-md-cleanup stops when `.claude` is a symlink, its project-choice check is coarse, it doesn't merge hard-linked AGENTS.md files, only SKILL.md gives a move target its resolved path, and a broken `.git` file blocks move targets below it |
| [TD-0009](TD-0009-agents-md-cleanup-doc-destinations.md) | Open | 2026-10-28 | Low | agents-md-cleanup's doc page choice, root `references` seed, output-read test detection, over-limit re-dispatch, unchecked user splits, unrecorded over-limit files, word-count scanner edge cases, missing size tests, the uncapped `(whole)` fix, an overlapping eval statement and `_docs` cap are known gaps |
| [TD-0010](TD-0010-implementation-manager-owner-queue-known-gaps.md) | Open | 2026-10-30 | Low | implementation-manager owner-queue known gaps: duplicated ref scope and id formatting, quiet `item drop` and `stop`, loose refusal tests, skill rules that disagree or need an extra `status`, and a thin `owner-queue` eval |
| [TD-0011](TD-0011-plugin-data-per-repo-retention.md) | Open | 2026-11-05 | Low | branch-review and implementation-manager prune only the repo they run in, and implementation-manager judges a run's liveness by age alone |
| [TD-0012](TD-0012-implementation-manager-context-from-transcript.md) | Open | 2026-11-09 | Low | The implementation-manager `context` line parses the transcript's undocumented JSON layout, so a layout change silently stops compact prompts |
| [TD-0013](TD-0013-implementation-manager-recorded-check-path.md) | Open | 2026-11-09 | Low | The implementation-manager recorded `rs` path goes stale when branch-review updates mid-run, and each event then costs a fallback check |
| [TD-0014](TD-0014-implementation-manager-compaction-skill-limit.md) | Open | 2026-11-09 | Low | The implementation-manager compaction design relies on the 5,000-token skill re-injection limit documented on 2026-11-09 |
| [TD-0015](TD-0015-implementation-manager-eval-gaps.md) | Open | 2026-11-09 | Low | implementation-manager evals: `check-ran` fired in 4 of 7 push-gate runs, runs with a background `wait` hang, hold judge split, unscored verdict check, resume grader passes baseline 2 of 3
| [TD-0016](TD-0016-branch-review-measuring-tools-known-limits.md) | Open | 2026-11-09 | Low | branch-review's usage report and small replay have known limits: a replay baseline that overstates savings, a major-miss gate stricter than run variance, Phase 1 shipped on real-use judgment with a possible loss of test-breaking depth, a headless run that differs from a subagent, a judge with Bash that shares `.git`, placeholder Haiku and Fable prices, double-counted runs, a one-line recovery, and a reviewer 1 byte under its cap |

## Columns

| Column | Contents |
|----|----|
| ID | `TD-NNNN`, linked to the item's file in this folder |
| Status | `Open` (nobody works on it), `In progress`, or `Blocked` (it waits for something that the item file names) |
| Added | The date when the item was added, `YYYY-MM-DD` |
| Impact | `Low` (cosmetic or local), `Medium` (makes work slower or can cause defects), `High` (can cause incorrect results, security problems or data loss) |
| Description | One line: what is wrong |
