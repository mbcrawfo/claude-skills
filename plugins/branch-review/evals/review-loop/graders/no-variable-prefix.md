---
type: tool_used
tool: Bash
input_match: '\b\w+=\\?["'']?python3\s[^;&|]*review_state\.py'
min: 0
max: 0
---

No Bash call stored the multi-word `python3 …/review_state.py` prefix in a shell variable, as v0.7 sessions did with `RS="python3 …"; $RS locate`. zsh does not split such a variable into words, so the call fails with exit 127. A variable holding only the `rs` path works, because it is one word. This grader does not flag it, nor variables the reviewer uses for other tools (such as `G=/opt/homebrew/bin/git; $G status`), since the trace includes subagent calls.
