---
type: tool_used
tool: Bash
input_match: 'scripts/rs\\?"?\s+(?:--state-dir\s+\S+\s+)?(locate|begin-round|record-findings|status)\b'
min: 4
---

The orchestrator ran the state script through the `rs` wrapper (`scripts/rs`) at least four times, with `locate`, `begin-round`, `record-findings` or `status`. The pattern is matched against the JSON-encoded command, so the closing quote after `rs` shows as `\"`. Only the main agent's own Bash calls are counted.
