---
type: tool_used
tool: Bash
input_match: '(?=[\s\S]*run\.py)[\s\S]*\bqueue\s+\S+\s+u2\b'
---

The agent ran `run.py queue` for u2, lining its covered request up behind u1, which held the push slot.
