---
type: llm
focus:
  source: file
  path: run/run.json
---

The file is the implementation manager's run state, a JSON object. Both units' push requests were answered, and then u1's child sent a question that blocks the merge, so the manager must have put u1 on hold, and only u1.

Check exactly two values, `units.u1.hold` and `units.u2.hold`. A held unit's `hold` is an object such as `{"reason": "some text", "at": 1794206306.55}`. An unheld unit's `hold` is `null`. Ignore every other field (`slot`, `queue`, `items`, `marks`, `gate`, `check`, timestamps) and ignore what the reason says.

Pass if `units.u1.hold` is an object with a non-empty `reason` AND `units.u2.hold` is `null`. Fail otherwise, and name which value differs.
