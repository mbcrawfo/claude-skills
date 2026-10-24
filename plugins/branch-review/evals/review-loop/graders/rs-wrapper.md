---
type: tool_used
tool: Bash
input_match: 'scripts/rs\b'
min: 4
---

The orchestrator ran the state script through the `rs` wrapper (`scripts/rs`) in at least four Bash calls, either by its full path or through a variable assigned that path in the same call. The reviewer subagent never runs `rs`, so its calls cannot satisfy this grader.
