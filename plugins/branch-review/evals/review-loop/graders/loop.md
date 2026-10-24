---
type: llm
focus:
  source: file
  path: review-summary.md
---

The file is the summary of a branch review followed by the agent's final report. Pass only if every one of these holds in it:

1. The review ran at least two rounds, and the last one was a follow-up or a second opinion: the summary's first line says `N rounds` with N of 2 or more, and ends with `last followup` or `last deep (second opinion)`. A second-opinion round (`deep (second opinion)` in the header or the body) counts as a round.
2. Each of the four planted defects appears as a finding, under any section:
   - the off-by-one: the bulk tier uses `quantity > 10` while the intent says 10 or more units;
   - the float sibling: `discounted_total` calls `apply_percent_off` (in `src/shop/money.py`), which computes money in float against the `src/AGENTS.md` rule that money is `Decimal`;
   - the vacuous test: the bulk-order test only asserts the result is not `None`;
   - the negative-quantity gap: the intent says negative quantities raise `ValueError` and no code does that.
3. Each of those four findings is under "Fixed and verified", or under "Escalated" with what the developer must decide. None is left only under "Open and unverified".
4. The summary has an "Intent check" section.
5. The final report lists the escalated and invalid findings, or says there are none.

Fail if any item is missing, and name the missing item.
