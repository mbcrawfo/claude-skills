# TD-0012: branch-review measuring tools: known limits

## Problem

The branch-review [usage report](../../plugins/branch-review/evals/usage/usage_report.py) and [small replay](../../plugins/branch-review/evals/replay/replay.py) have these known, unresolved limits:

- The replay's real `claude` path (the reviewer and judge calls, and the `total_cost_usd` it reads) has not run. It is unexercised until the first owner-approved run.
- A headless `claude -p --agent` run is not the same as a subagent dispatch. It loads the main-thread system prompt and settings hooks. The replay runs it in a temp worktree of the replayed repo, outside every checkout, so only that repo's own project instructions load, as in a real round, and no other checkout's. So replay cost is close to a real round's, not equal to it.
- The judge has Bash, in a worktree that shares `.git` with the real repo.
- The usage report prices Haiku and Fable at placeholder rates (`usage_report.PRICES`), which are not checked against current list prices.
- The usage report lets two rounds with the same range, mode and focus both claim the same reviewer runs, so those runs are counted twice.
- With the one-line final message, the "no json block" recovery in section 6.2 of the review skill saves only that line, so the round is recorded without prose.
- [`reviewer.md`](../../plugins/branch-review/agents/reviewer.md) is 10 bytes under its 24,000-byte test cap, so any addition fails the test.

## Why it exists

The tools were built as the smallest measure that Phase 1 needed, and a real run costs tens of dollars, so the owner approves the first one. The other limits were seen in review and left for later.

## Impact

Low. The tools are for contributors and do not change review results. The risks are a first replay run that fails on the real `claude` path, a replay cost that reads a little low or high, a double-counted usage row, and a Haiku or Fable cost that reads wrong.

## Proposed fix

- Run the replay once with owner approval and fix what the real path shows.
- Run the judge without Bash, or in a copy of the repo that does not share `.git`.
- Set the Haiku and Fable rows in `usage_report.PRICES` from current list prices.
- Assign each reviewer run to one round in the usage report, for example by run id or by time order.
- Keep the prose in the report file, or have the reviewer's one line carry the verdict, so 6.2 recovers more than one line.
- Move the reviewer's reference text out of `reviewer.md`, or raise the cap with a reason.

## Trigger

The first owner-approved replay run, or any change to `reviewer.md`.
