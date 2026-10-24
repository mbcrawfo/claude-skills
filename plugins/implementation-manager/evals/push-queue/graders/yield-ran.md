---
type: tool_used
tool: Bash
input_match: 'run\.py[^\sA-Za-z0-9]*\s+yield\s.*\bu1\b'
---

The agent ran `run.py yield` for u1, giving up its push slot while its escalated finding waits on the owner.
