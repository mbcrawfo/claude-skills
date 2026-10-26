---
type: tool_used
tool: Write
input_match: 'round-\d+\.md'
min: 0
max: 0
---

The orchestrator never wrote a round report itself. The reviewer has no Write tool, so a Write to `round-<n>.md` is the orchestrator's fallback for a reviewer that skipped `REPORT_PATH`; 0.9 makes every mode end by writing it.
