---
type: tool_used
tool: Bash
input_match: 'run\.py[^\sA-Za-z0-9]*\s+queue\s.*\bu2\b'
---

The agent ran `run.py queue` for u2, lining its covered request up behind u1, which held the push slot.
