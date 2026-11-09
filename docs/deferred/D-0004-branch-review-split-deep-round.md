# D-0004: branch-review split deep round and second-opinion decision

## Planned

The [split deep round design](../superpowers/specs/2026-11-08-branch-review-split-deep-round-design.md) has three phases. Phases 2 and 3 are not built:

- **Phase 2, the split deep round.** A deep round without the second-opinion focus dispatches three reviewers in parallel (`code`, `tests`, `docs-intent`), each with a part of the checklist and its own detached worktree. A `deep_round` option (`single` default, or `split`) chooses the shape. The round is recorded from several reports, with duplicates handled in triage.
- **Phase 3, the second-opinion decision.** Once split is the default, the usage report tracks second opinions after split rounds. After 15 of them, the automatic trigger is narrowed or the gap is recorded.

## Delivered instead

Phase 1 only, in branch-review 0.15.0:

- The shared efficiency changes: parallel tool calls, a short reviewer-facing [`report-schema.md`](../../plugins/branch-review/skills/review/report-schema.md), no re-read of added files and a one-line final message ([`reviewer.md`](../../plugins/branch-review/agents/reviewer.md)).
- The [usage report](../../plugins/branch-review/evals/usage/usage_report.py).
- The [small replay](../../plugins/branch-review/evals/replay/replay.py).

Every deep round still uses one reviewer.

## Why deferred

The design builds Phase 2 only after Phase 1 has run in real use. The usage report must first show that Phase 1 did not lower the majors found per first deep round (baseline 0.58).

## Remaining work

- The split deep round: three reviewers dispatched in one message, with the checklist categories and required steps divided as the design's table shows. Carried `OPEN_FINDINGS` go to `code` only.
- The `deep_round` user option and its storage on the round by `begin-round`.
- Multi-part recording: `record-findings` and `check-report` accept several reports; `merge_risk` is the highest of the parts, `intent_check`, `coverage` and `not_checked` are unions; ids are numbered across parts in a fixed order. A failed part is dispatched again once, alone; a split round is never recorded with fewer than 16 coverage items.
- Duplicates as their own outcome in triage, following the original's outcome.
- One detached worktree per part, created before dispatch and removed after recording.
- The small replay of the split, then the real-use ship gate: at least 15 split first deep rounds, with majors per round at least the single rate, second opinions after split rounds opening majors at no more than 0.45 each, at most one invalid finding, and median cost at most 70% of single in the same size bucket.
- Phase 3: after 15 second opinions that follow split rounds, narrow the automatic trigger if they open majors at 0.15 each or less, otherwise record the gap in `docs/tech-debt`.

## Trigger

The usage report shows at least 15 Phase 1 first deep rounds with majors per round not below 0.58.
