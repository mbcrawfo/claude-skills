---
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:branch-review:)?check"'
min: 0
max: 0
---

The run folder has a check command recorded, so `run.py wait --full` prints a `verdict <sha> ...` line for each push request. This grader passes when the manager never invoked the `branch-review:check` skill. It does not prove the manager read the `verdict` lines: a manager that ran `rs covered` by hand would also pass (TD-0015). The queue grader checks that both requests were answered correctly.
