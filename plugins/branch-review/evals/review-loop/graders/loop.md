---
type: llm
focus: trace
---

Pass only if every one of these holds in the transcript:

1. A reviewer subagent was dispatched with no prior context: a fresh Agent dispatch whose prompt carries the intent, the diff range and the files to review, not a review the main agent did itself.
2. Each of the four planted defects was found:
   - the off-by-one: the bulk tier uses `quantity > 10` while the intent says 10 or more units;
   - the float sibling: `discounted_total` calls `apply_percent_off`, which computes money in float against the `src/AGENTS.md` rule that money is `Decimal`;
   - the vacuous test: the bulk-order test only asserts the result is not `None`;
   - the negative-quantity gap: the intent says negative quantities raise `ValueError` and no code does that.
3. The fixes were verified by a second review: another reviewer dispatch after the fixes, which checked the earlier findings against the fixed code.
4. The final message lists the escalated and invalid findings, or says there are none.

Fail if any item is missing, and name the missing item.
