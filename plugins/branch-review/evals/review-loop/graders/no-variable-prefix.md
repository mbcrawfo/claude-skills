---
type: tool_used
tool: Bash
input_match: '(?:^|[\s;&"{(])\w+=\\?"?[^\s;]*scripts/rs|\$\{?\w+\}?\s+(?:locate|begin-round|record-findings|status|snapshot|summary|prune|set-status|discuss|resolve)\b'
min: 0
max: 0
---

The orchestrator never stored the `rs` path in a shell variable (`RS=.../scripts/rs`, then `$RS locate`). Variables do not survive between Bash calls, and zsh does not split `$RS locate` into words, so the call fails. The pattern matches an assignment of the `scripts/rs` path to a variable, or a variable used as the command word of an `rs` subcommand. It does not match a plain `scripts/rs` call.
