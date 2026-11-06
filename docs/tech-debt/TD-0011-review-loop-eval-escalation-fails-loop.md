# TD-0011: The review-loop eval fails when the orchestrator escalates a finding

## Problem

The `loop` grader of the branch-review `review-loop` eval ([`graders/loop.md`](../../plugins/branch-review/evals/review-loop/graders/loop.md)) contradicts itself. Item 3 accepts a planted finding under "Escalated". Item 1 requires a second-opinion round. A second opinion never runs while a finding is escalated (`second_opinion_held` in [`review_state.py`](../../plugins/branch-review/scripts/review_state.py)), and an unattended eval run has no developer to answer the escalation. So a run that escalates any finding stops at `awaiting-decisions` after the follow-up and fails `loop`, though the skill did what it says.

The float finding (`apply_percent_off` computes money in float) invites this: it has two fair fixes, rewrite the shared helper in `Decimal` or compute inline in `discounted_total`, and the fixture's intent does not choose. The skill escalates a fix choice the intent does not settle. A run on 2026-11-06 (plugin 0.13.0) did this, escalated the vacuous-test finding with it, and scored 6/7. The run before it fixed both and passed.

## Why it exists

The grader was written for the path where the orchestrator fixes all four planted defects. The escalation hold on the second opinion came later and was not checked against it. It was found while checking 0.13.0's reviewer self-check, which the failing run did exercise: both reviewers ran `check-report` and passed it first time.

## Impact

Low. The eval is flaky: a correct run can fail, so a failing score does not show a regression without reading the summary. It costs a re-run (about $1 to $2) or a manual read each time.

## Proposed fix

Pick one:

- Make the fixture's `INTENT.md` settle the float fix ("`apply_percent_off` itself must return a `Decimal` rounded `ROUND_HALF_UP`"), so the skill fixes it instead of escalating. This keeps the second opinion as a required step.
- Or change item 1 of `loop.md` to accept a run that stops at `awaiting-decisions` with the second opinion held, when every planted finding is fixed and verified or escalated with what the developer must decide.

The first is better: it keeps the eval covering the second-opinion round.

## Trigger

The next change to the review-loop eval, or the next time it fails on `loop` with an escalated finding.
