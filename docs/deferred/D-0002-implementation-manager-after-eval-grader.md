# D-0002: Implementation manager eval for a unit refused the slot by `--after`

## Planned

The [implementation manager phase 8 review design](../superpowers/specs/2026-10-25-implementation-manager-phase-8-review-design.md) ("README and evals") asked for the `push-queue` eval case to get at least one grader for a held unit and one for an `--after` unit refused the slot, or a new case. That covers the manager reading `run.py go`'s `merges after` refusal and answering with `run.py queue`, including a slot holder that gains `--after` mid-run.

## Delivered instead

The held-unit graders shipped ([`hold.md`](../../plugins/implementation-manager/evals/push-queue/graders/hold.md) and [`hold-ran.md`](../../plugins/implementation-manager/evals/push-queue/graders/hold-ran.md)). The `--after` behaviour is covered by unit tests only, in [`test_runstate.py`](../../plugins/implementation-manager/tests/test_runstate.py) (`AfterTests`, `SlotHolderAfterTests`) and [`test_run_cli.py`](../../plugins/implementation-manager/tests/test_run_cli.py) (`test_after_on_the_slot_holder_frees_the_slot`, `test_status_next_skips_queue_entries_that_are_not_mergeable`). They test the script; nothing tests that the manager follows SKILL.md's "Answering a push request" step 6 when `go` refuses with `merges after`.

## Why deferred

The 0.4.0 release's final review found the grader missing and ruled to ship the release with the unit tests, since the script refusals they cover are what keep the queue correct (2026-10-25).

## Remaining work

Extend [`push-queue`](../../plugins/implementation-manager/evals/push-queue/) or add a case:

- In `scaffold.sh`, give one unit `--after` on a unit that has not merged, with a covered push request from it.
- In the prompt, have the manager answer that request. A further step adds `--after` to the slot holder mid-run (`run.py unit <holder> --after <other>`) and has both units ask again.
- Graders: a `tool_used` grader for `run.py queue` on the `--after` unit after `go` refused it; a file grader on `run/pushes.log` that the `--after` unit got no `go` while its `--after` unit was unmerged; a file grader on `run/run.json` that the slot is not held by a unit whose `after` units are unmerged.
- Run the case with `claude plugin eval` and record the result.

## Trigger

The next change to the push queue or to "Answering a push request", or the next eval pass over this plugin.
