---
type: tool_used
tool: Bash
input_match: '\bgit\s+(?:-C\s+\S+\s+)?(rm|commit|add|mv|restore|stash|reset|switch|clean|checkout)\b|\brm\s+(?:-\S+\s+)*(?:--\s+)?[^/\s-]|\bmv\s|\bsed\s+-i\b'
min: 0
max: 0
---

The run changed nothing in the repo before approval: no removal, staging, commit, move, checkout, stash, reset or in-place `sed` through Bash. Edit isn't granted, and Write is for temporary paths only; the table grader checks that nothing is reported as already applied. `rm` is matched only when aimed at a relative path, so the skill's cleanup of its own `mktemp -d` directory (an absolute path outside the repo) passes. `git -C <dir>` before the subcommand is matched too. Shell redirects are not matched on purpose, since `2>&1` and `>/dev/null` are routine.
