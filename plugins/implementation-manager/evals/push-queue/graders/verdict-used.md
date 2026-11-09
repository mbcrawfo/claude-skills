---
type: tool_used
tool: Skill
input_match: '"skill"\s*:\s*"(?:branch-review:)?check"'
min: 0
max: 0
---

The run folder has a check command recorded, so `run.py wait --full` prints a `verdict <sha> ...` line for each push request. The manager answered both requests from those lines and never invoked the `branch-review:check` skill. (The queue grader checks that both requests were answered.)
