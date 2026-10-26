---
type: llm
focus:
  source: file
  path: run/run.json
---

The file is the implementation manager's run state. Both units' push requests were answered, and then u1's child sent a question that blocks the merge (an escalated finding the owner has not answered), so the manager must have put u1 on hold, and only u1. A held unit's entry in `units` has a `hold` field that is an object with a `reason` and an `at` time; an unheld unit's `hold` is `null`.

Pass only if `units.u1.hold` is a non-null object whose `reason` is a non-empty string, and `units.u2.hold` is `null`. Fail if `units.u1.hold` is `null` or has an empty reason, or if `units.u2.hold` is not `null`. Name which field differs.
