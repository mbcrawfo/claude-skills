# TD-0016: branch-review measuring tools: known limits

## Problem

The branch-review [usage report](../../plugins/branch-review/evals/usage/usage_report.py) and [small replay](../../plugins/branch-review/evals/replay/replay.py) have these known, unresolved limits:

- The replay's cost gate compares a replay with the historical round, but a headless replay costs about half as much as a real round even for the same reviewer. On 2026-11-09, replays of the 0.14.0 reviewer cost $2.77 and $2.87 against $4.49 and $6.20 for the same rounds. So the gate overstates savings.
- The replay's "no round-1 major missed" gate is stricter than run-to-run variance allows on one run per branch. In replay, the 0.14.0 reviewer itself missed one of the two round-1 majors of `claude/td-0112-pg-runtime-locks-ee36bb`.
- Phase 1 (0.15.0) shipped without passing its replay gate, by owner decision on 2026-11-09, to be judged in real use. Against fresh 0.14.0 replays, it cost 14% to 35% less per round. It caught the `maintenance`-suite major once told to run every suite. On td-0112 it found one of two majors once and neither once, with fewer test breakages tried (2 against 3) and fewer turns (25 against 38). That may be variance or a loss of depth in the shortened "break each new test" step.
- A headless `claude -p --agent` run is not the same as a subagent dispatch. It loads the main-thread system prompt and settings hooks. The replay runs it in a temp worktree of the replayed repo, outside every checkout, so only that repo's own project instructions load, as in a real round, and no other checkout's. So replay cost is close to a real round's, not equal to it.
- The judge has Bash, in a worktree that shares `.git` with the real repo.
- The usage report prices Haiku and Fable at placeholder rates (`usage_report.PRICES`), which are not checked against current list prices.
- The usage report lets two rounds with the same range, mode and focus both claim the same reviewer runs, so those runs are counted twice.
- With the one-line final message, the "no json block" recovery in section 6.2 of the review skill saves only that line, so the round is recorded without prose.
- [`reviewer.md`](../../plugins/branch-review/agents/reviewer.md) is 1 byte under its 24,000-byte test cap, so any addition fails the test.

## Why it exists

The tools were built as the smallest measure that Phase 1 needed, without re-running a baseline, because the owner wanted each check to cost tens of dollars at most. The other limits were seen in review and left for later.

## Impact

Low to medium. The tools are for contributors, but the replay decides whether a reviewer version ships. The risks are a replay that overstates savings or fails a version for ordinary variance, a possible small loss of test-breaking depth in 0.15.0, a double-counted usage row, and a Haiku or Fable cost that reads wrong.

## Proposed fix

- Score the replay against a fresh replay of the previous version on the same branches, not the historical round, and judge recall over two or more runs per branch.
- If the usage report shows fewer than 0.58 majors per first deep round for 0.15.0, restore the original "break each new test" wording in `reviewer.md`.
- Run the judge without Bash, or in a copy of the repo that does not share `.git`.
- Set the Haiku and Fable rows in `usage_report.PRICES` from current list prices.
- Assign each reviewer run to one round in the usage report, for example by run id or by time order.
- Keep the prose in the report file, or have the reviewer's one line carry the verdict, so 6.2 recovers more than one line.
- Move the reviewer's reference text out of `reviewer.md`, or raise the cap with a reason.

## Trigger

A usage report for 0.15.0 with at least 15 first deep rounds, the next replay of a reviewer version, or any change to `reviewer.md`.
